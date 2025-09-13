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
            languages="en,fr",
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
        self.assertContains(response, reverse("django-admindocs-manuals"))

    def test_manuals_page_in_admin_context(self):
        response = self.client.get(reverse("django-admindocs-manuals"))
        self.assertContains(response, reverse("django-admindocs-docroot"))
        self.assertContains(response, "Test Manual")
        self.assertContains(response, "Test description")
        self.assertContains(response, "Languages: en,fr")
        self.assertContains(
            response, reverse("django-admindocs-manual-pdf", args=["test-manual"])
        )
        self.assertContains(response, 'id="nav-sidebar"')
        self.assertContains(response, 'id="nav-filter"')
        self.assertNotContains(
            response, "You don't have permission to view or edit anything."
        )

    def test_manual_detail_in_admin_context(self):
        response = self.client.get(
            reverse("django-admindocs-manual-detail", args=["test-manual"])
        )
        self.assertContains(response, reverse("django-admindocs-manuals"))
        self.assertContains(response, "hi")
        self.assertContains(
            response, reverse("django-admindocs-manual-pdf", args=["test-manual"])
        )
        self.assertContains(response, 'id="nav-sidebar"')
        self.assertContains(response, 'id="nav-filter"')
        self.assertNotContains(
            response, "You don't have permission to view or edit anything."
        )

    def test_public_manual_list(self):
        response = self.client.get(reverse("man:list"))
        self.assertContains(response, "Test Manual")
        self.assertContains(response, "Test description")
        self.assertContains(response, "Languages: en,fr")
        self.assertContains(response, reverse("man:manual-pdf", args=["test-manual"]))
        self.assertNotContains(response, 'id="nav-sidebar"')

    def test_public_manual_detail(self):
        response = self.client.get(reverse("man:manual-html", args=["test-manual"]))
        self.assertContains(response, "hi")
        self.assertContains(response, reverse("man:manual-pdf", args=["test-manual"]))
        self.assertNotContains(response, 'id="nav-sidebar"')
