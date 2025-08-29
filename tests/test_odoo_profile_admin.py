from django.test import TestCase
from django.contrib.auth import get_user_model

from core.models import OdooProfile
from core.admin import OdooProfileAdminForm


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
