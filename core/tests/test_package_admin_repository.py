from unittest import mock

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from core.models import Package


class PackageAdminRepositoryTests(TestCase):
    def setUp(self):
        super().setUp()
        original_init = forms.URLField.__init__

        def _init_with_http_default(self, *args, **kwargs):
            kwargs.setdefault("assume_scheme", "http")
            return original_init(self, *args, **kwargs)

        self.addCleanup(setattr, forms.URLField, "__init__", original_init)
        forms.URLField.__init__ = _init_with_http_default
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.package = Package.objects.create(name="pkg")

    def test_repository_creation_updates_package(self):
        url = reverse("admin:core_package_create_repository", args=[self.package.pk])
        with mock.patch(
            "core.admin.create_repository_for_package",
            return_value="https://github.com/example/pkg",
        ) as create_repo:
            response = self.client.post(
                url,
                {
                    "owner_repo": "example/pkg",
                    "description": "Example package",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.package.refresh_from_db()
        self.assertEqual(self.package.repository_url, "https://github.com/example/pkg")

        args, kwargs = create_repo.call_args
        self.assertEqual(args[0], self.package)
        self.assertEqual(kwargs["owner"], "example")
        self.assertEqual(kwargs["repo"], "pkg")
        self.assertFalse(kwargs["private"])
        self.assertEqual(kwargs["description"], "Example package")

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("GitHub repository created" in str(message) for message in messages)
        )

    def test_repository_errors_surface_message(self):
        original_url = self.package.repository_url
        url = reverse("admin:core_package_create_repository", args=[self.package.pk])
        with mock.patch(
            "core.admin.create_repository_for_package",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(
                url,
                {
                    "owner_repo": "example/pkg",
                    "description": "Broken",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.package.refresh_from_db()
        self.assertEqual(self.package.repository_url, original_url)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("GitHub repository creation failed" in str(message) for message in messages)
        )

    def test_admin_action_redirects_to_repository_form(self):
        url = reverse("admin:core_package_changelist")
        response = self.client.post(
            url,
            {
                "action": "create_repository_bulk_action",
                "_selected_action": [str(self.package.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("admin:core_package_create_repository", args=[self.package.pk]),
        )

    def test_admin_action_requires_single_selection(self):
        other = Package.objects.create(name="other")
        url = reverse("admin:core_package_changelist")
        response = self.client.post(
            url,
            {
                "action": "create_repository_bulk_action",
                "_selected_action": [str(self.package.pk), str(other.pk)],
            },
            follow=True,
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Select exactly one package" in str(message) for message in messages)
        )
