"""Regression coverage for admin user impersonation tools."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.impersonation import IMPERSONATOR_SESSION_KEY


class UserImpersonationAdminTests(TestCase):
    """Validate superuser impersonation flows from the users admin."""

    def setUp(self):
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="root-admin",
            email="root-admin@example.com",
            password="Password123",
        )
        self.staff_user = user_model.objects.create_user(
            username="staff-user",
            email="staff-user@example.com",
            password="Password123",
            is_staff=True,
        )
        self.target_user = user_model.objects.create_user(
            username="target-user",
            email="target-user@example.com",
            password="Password123",
            is_staff=False,
        )

    def test_superuser_can_impersonate_user_from_admin_url(self):
        self.client.force_login(self.superuser)

        url = reverse("admin:core_user_impersonate", args=[self.target_user.pk])
        response = self.client.post(
            url,
            data={"next": reverse("admin:index")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:index"))
        session = self.client.session
        self.assertEqual(session.get(IMPERSONATOR_SESSION_KEY), self.superuser.pk)
        self.assertEqual(int(session.get("_auth_user_id")), self.target_user.pk)

    def test_stop_impersonation_restores_original_superuser(self):
        self.client.force_login(self.superuser)
        self.client.post(reverse("admin:core_user_impersonate", args=[self.target_user.pk]))

        response = self.client.post(reverse("stop-impersonation"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:index"))
        session = self.client.session
        self.assertNotIn(IMPERSONATOR_SESSION_KEY, session)
        self.assertEqual(int(session.get("_auth_user_id")), self.superuser.pk)

    def test_stop_impersonation_logs_out_when_impersonator_missing(self):
        self.client.force_login(self.superuser)
        self.client.post(reverse("admin:core_user_impersonate", args=[self.target_user.pk]))
        self.superuser.delete()

        response = self.client.post(reverse("stop-impersonation"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:index"))
        session = self.client.session
        self.assertNotIn(IMPERSONATOR_SESSION_KEY, session)
        self.assertNotIn("_auth_user_id", session)

    def test_staff_without_superuser_access_cannot_impersonate(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse("admin:core_user_impersonate", args=[self.target_user.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_change_form_exposes_impersonate_button_for_superuser(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("admin:users_user_change", args=[self.target_user.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("admin:core_user_impersonate", args=[self.target_user.pk]),
        )

    def test_impersonate_view_get_does_not_switch_session(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("admin:core_user_impersonate", args=[self.target_user.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("admin:users_user_change", args=[self.target_user.pk]),
        )
        session = self.client.session
        self.assertNotIn(IMPERSONATOR_SESSION_KEY, session)
        self.assertEqual(int(session.get("_auth_user_id")), self.superuser.pk)

    def test_impersonation_next_url_rejects_external_target(self):
        self.client.force_login(self.superuser)

        response = self.client.post(
            reverse("admin:core_user_impersonate", args=[self.target_user.pk]),
            data={"next": "https://evil.example/phish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")

    def test_stop_impersonation_next_url_rejects_external_target(self):
        self.client.force_login(self.superuser)
        self.client.post(reverse("admin:core_user_impersonate", args=[self.target_user.pk]))

        response = self.client.post(
            reverse("stop-impersonation"),
            data={"next": "https://evil.example/phish"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:index"))
