import os
import sys
from pathlib import Path

import django
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest import mock

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django.setup()

from pages.views import CustomLoginView


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

    def test_admin_login_page_has_public_login_link(self):
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("pages:login"))
        self.assertContains(response, "Login on Public Site")


class LoginViewRedirectFieldTests(TestCase):
    def test_custom_redirect_field_name_preserved(self):
        request = RequestFactory().get(
            reverse("pages:login"), {"continue": "/special/"}
        )
        request.user = AnonymousUser()

        view = CustomLoginView()
        view.redirect_field_name = "continue"
        view.setup(request)

        with mock.patch("pages.views.mailer.can_send_email", return_value=False):
            context = view.get_context_data()

        self.assertEqual(context["continue"], "/special/")
        self.assertEqual(context["next"], "/special/")
