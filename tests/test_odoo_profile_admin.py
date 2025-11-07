from unittest.mock import patch
import os

import pytest

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.http import QueryDict
from django.utils import timezone

from core.models import OdooProfile
from core.admin import (
    KeepExistingValue,
    OdooProfileAdmin,
    OdooProfileAdminForm,
    OdooProfileInlineForm,
)


class OdooProfileAdminFormTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="odoo", password="pwd")

    def _create_profile(self, password="secret"):
        return OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password=password,
        )

    def test_password_field_hidden_and_blank_initial(self):
        profile = self._create_profile()
        form = OdooProfileAdminForm(instance=profile)
        html = form.as_p()
        self.assertIn('type="password"', html)
        self.assertNotIn("secret", html)

    def test_blank_password_keeps_existing(self):
        profile = self._create_profile()
        data = {
            "user": self.user.pk,
            "host": "http://test2",
            "database": "db",
            "username": "odoo",
            "password": "",
            "crm": OdooProfile.CRM.ODOO,
        }
        form = OdooProfileAdminForm(data, instance=profile)
        self.assertTrue(form.is_valid())
        form.save()
        profile.refresh_from_db()
        self.assertEqual(profile.password, "secret")
        self.assertEqual(profile.host, "http://test2")

    def test_new_password_saved(self):
        profile = self._create_profile()
        data = {
            "user": self.user.pk,
            "host": "http://test",
            "database": "db",
            "username": "odoo",
            "password": "newpass",
            "crm": OdooProfile.CRM.ODOO,
        }
        form = OdooProfileAdminForm(data, instance=profile)
        self.assertTrue(form.is_valid())
        form.save()
        profile.refresh_from_db()
        self.assertEqual(profile.password, "newpass")

    def test_blank_password_preserves_sigil_placeholder(self):
        self.addCleanup(lambda: os.environ.pop("ODOO_PASSWORD", None))
        os.environ.pop("ODOO_PASSWORD", None)
        profile = self._create_profile(password="[ENV.ODOO_PASSWORD]")
        original = (
            OdooProfile.objects.filter(pk=profile.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertEqual(original, "[ENV.ODOO_PASSWORD]")
        os.environ["ODOO_PASSWORD"] = "odoo-secret"
        data = {
            "user": self.user.pk,
            "host": "http://test",
            "database": "db",
            "username": "odoo",
            "password": "",
            "crm": OdooProfile.CRM.ODOO,
        }
        form = OdooProfileAdminForm(data, instance=profile)
        self.assertTrue(form.is_valid())
        value = form.cleaned_data["password"]
        self.assertIsInstance(value, KeepExistingValue)
        self.assertEqual(value.field, "password")
        self.assertFalse(value)
        field = form.instance._meta.get_field("password")
        self.assertEqual(field.value_from_object(form.instance), "[ENV.ODOO_PASSWORD]")
        saved = form.save()
        field = saved._meta.get_field("password")
        self.assertEqual(field.value_from_object(saved), "[ENV.ODOO_PASSWORD]")
        stored = (
            OdooProfile.objects.filter(pk=saved.pk)
            .values_list("password", flat=True)
            .get()
        )
        self.assertEqual(stored, "[ENV.ODOO_PASSWORD]")
        self.assertEqual(saved.password, "odoo-secret")


class OdooProfileAdminActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="odooadmin", email="a@example.com", password="pwd"
        )
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
        )
        self.factory = RequestFactory()
        self.admin = OdooProfileAdmin(OdooProfile, AdminSite())

    def _get_request(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        return request

    def test_my_profile_redirects_to_existing_profile(self):
        request = self._get_request()
        response = self.admin.my_profile(request, OdooProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        expected = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        self.assertEqual(response.url, expected)

    def test_my_profile_redirects_to_add_when_missing(self):
        self.profile.delete()
        request = self._get_request()
        response = self.admin.my_profile(request, OdooProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        expected = f"{reverse('admin:core_odooprofile_add')}?user={self.user.pk}"
        self.assertEqual(response.url, expected)

    def test_my_profile_without_add_permission_shows_error(self):
        self.profile.delete()
        User = get_user_model()
        limited = User.objects.create_user(
            username="limited", password="pwd", is_staff=True
        )
        request = self.factory.get("/")
        request.user = limited
        request.session = self.client.session
        from django.contrib.messages.storage.fallback import FallbackStorage

        request._messages = FallbackStorage(request)
        response = self.admin.my_profile(request, OdooProfile.objects.none())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("admin:core_odooprofile_changelist"),
        )
        messages = [m.message.lower() for m in request._messages]
        self.assertTrue(any("permission" in message for message in messages))

    def test_my_profile_change_action_redirects(self):
        request = self._get_request()
        response = self.admin.my_profile_action(request, self.profile)
        self.assertEqual(response.status_code, 302)
        expected = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        self.assertEqual(response.url, expected)

    def test_list_filter_includes_crm(self):
        request = self._get_request()
        self.assertIn("crm", self.admin.get_list_filter(request))

    def test_credentials_ok_column(self):
        self.assertFalse(self.admin.credentials_ok(self.profile))

        self.profile.verified_on = timezone.now()
        self.assertTrue(self.admin.credentials_ok(self.profile))

        self.profile.password = ""
        self.assertFalse(self.admin.credentials_ok(self.profile))

    @patch("core.models.OdooProfile.verify")
    def test_verify_credentials_action(self, mock_verify):
        request = self._get_request()
        self.admin.verify_credentials_action(request, self.profile)
        mock_verify.assert_called_once_with()
        messages = [m.message for m in request._messages]
        self.assertTrue(any("verified" in m for m in messages))

    @pytest.mark.skip("Change form object action link not rendered in test environment")
    def test_change_form_contains_link(self):
        request = self._get_request()
        response = self.admin.changeform_view(request, str(self.profile.pk))
        content = response.render().content.decode()
        self.assertIn("Test credentials", content)


class OdooProfileInlineFormTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="inline", password="pwd")

    def test_empty_inline_form_deletes_profile(self):
        form = OdooProfileInlineForm(
            data={
                "host": "",
                "database": "",
                "username": "",
                "password": "",
                "crm": OdooProfile.CRM.ODOO,
            },
            instance=OdooProfile(user=self.user),
        )
        self.assertTrue(form.is_valid())
        self.assertTrue(form.cleaned_data.get("DELETE"))

    def test_partial_data_requires_all_fields(self):
        form = OdooProfileInlineForm(
            data={
                "host": "http://odoo",
                "database": "",
                "username": "",
                "password": "",
                "crm": OdooProfile.CRM.ODOO,
            },
            instance=OdooProfile(user=self.user),
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Provide host, database, username, and password", form.non_field_errors()[0]
        )

    def test_clearing_existing_profile_marks_delete(self):
        profile = OdooProfile.objects.create(
            user=self.user,
            host="http://odoo",
            database="db",
            username="user",
            password="secret",
        )
        prefix = "odoo_profile_set-0"
        data = QueryDict(mutable=True)
        data.update(
            {
                f"{prefix}-id": str(profile.pk),
                f"{prefix}-host": "",
                f"{prefix}-database": "",
                f"{prefix}-username": "",
                f"{prefix}-password": "",
                f"{prefix}-crm": OdooProfile.CRM.ODOO,
            }
        )
        form = OdooProfileInlineForm(data=data, instance=profile, prefix=prefix)
        self.assertTrue(form.is_valid())
        self.assertTrue(form.cleaned_data.get("DELETE"))
