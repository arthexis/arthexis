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
from nodes.models import Node, NodeRole
from pages.models import Application, Module, UserManual


class ManualTests(TestCase):
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
        app, _ = Application.objects.get_or_create(name="pages")
        module, _ = Module.objects.get_or_create(
            node_role=role, application=app, path="/man/"
        )
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
        self.assertContains(response, "Languages")
        self.assertContains(response, "en,fr")
        self.assertContains(
            response, reverse("django-admindocs-manual-pdf", args=["test-manual"])
        )
        self.assertContains(response, "Download PDF")
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
        self.assertContains(response, "Download PDF")
        self.assertContains(response, 'id="nav-sidebar"')
        self.assertContains(response, 'id="nav-filter"')
        self.assertNotContains(
            response, "You don't have permission to view or edit anything."
        )

    def test_public_manual_list(self):
        response = self.client.get(reverse("pages:manual-list"))
        self.assertContains(response, "Test Manual")
        self.assertContains(response, "Test description")
        self.assertContains(response, "Languages")
        self.assertContains(response, "en,fr")
        self.assertContains(
            response, reverse("pages:manual-pdf", args=["test-manual"])
        )
        self.assertContains(response, "Download PDF")
        self.assertNotContains(response, 'id="nav-sidebar"')

    def test_public_manual_detail(self):
        response = self.client.get(reverse("pages:manual-detail", args=["test-manual"]))
        self.assertContains(response, "hi")
        self.assertContains(
            response, reverse("pages:manual-pdf", args=["test-manual"])
        )
        self.assertContains(response, "Download PDF")
        self.assertNotContains(response, 'id="nav-sidebar"')

    def test_manual_pdf_download(self):
        response = self.client.get(reverse("pages:manual-pdf", args=["test-manual"]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="test-manual.pdf"',
        )

    def test_manual_detail_includes_word_wrap_styles(self):
        manual = UserManual.objects.get(slug="test-manual")
        manual.pdf_orientation = UserManual.PdfOrientation.PORTRAIT
        manual.save()

        response = self.client.get(reverse("pages:manual-detail", args=[manual.slug]))
        self.assertContains(response, "manual-content")
        self.assertContains(response, "overflow-wrap: anywhere;")
        self.assertContains(response, "size: portrait;")

    def test_admin_manual_detail_uses_orientation_styles(self):
        manual = UserManual.objects.get(slug="test-manual")
        manual.pdf_orientation = UserManual.PdfOrientation.PORTRAIT
        manual.save()

        response = self.client.get(
            reverse("django-admindocs-manual-detail", args=[manual.slug])
        )
        self.assertContains(response, "manual-content")
        self.assertContains(response, "overflow-wrap: anywhere;")
        self.assertContains(response, "size: portrait;")
