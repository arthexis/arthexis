"""Tests for ops redirect hardening."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.ops.models import OperationScreen


class OpsRedirectTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="ops-staff",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff)

    def test_clear_active_operation_blocks_external_next_url(self):
        response = self.client.get(
            reverse("ops:clear-active"),
            {"next": "https://malicious.example/phish"},
        )

        self.assertRedirects(response, reverse("admin:index"))

    def test_start_operation_view_blocks_external_start_url(self):
        operation = OperationScreen.objects.create(
            title="Unsafe",
            slug="unsafe",
            description="Unsafe start URL",
            start_url="https://malicious.example/",
            is_active=True,
        )

        response = self.client.get(
            reverse("admin:ops_operationscreen_start", args=[operation.pk])
        )

        self.assertRedirects(response, reverse("admin:index"))

    def test_clear_active_operation_allows_relative_next_url_without_slash(self):
        response = self.client.get(
            reverse("ops:clear-active"),
            {"next": "foo"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "foo")

    def test_start_operation_view_allows_relative_start_url_without_slash(self):
        operation = OperationScreen.objects.create(
            title="Relative",
            slug="relative",
            description="Relative start URL",
            start_url="foo",
            is_active=True,
        )

        response = self.client.get(
            reverse("admin:ops_operationscreen_start", args=[operation.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "foo")
