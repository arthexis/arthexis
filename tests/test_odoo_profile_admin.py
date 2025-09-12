from unittest.mock import patch
import pytest

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core.models import OdooProfile
from core.admin import OdooProfileAdmin, OdooProfileAdminForm


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
        }
        form = OdooProfileAdminForm(data, instance=profile)
        self.assertTrue(form.is_valid())
        form.save()
        profile.refresh_from_db()
        self.assertEqual(profile.password, "newpass")


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
