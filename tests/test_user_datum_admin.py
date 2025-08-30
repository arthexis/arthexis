from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType

from core.models import OdooProfile
from core.user_data import UserDatum


class UserDatumAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("udadmin", password="pw")
        self.client.login(username="udadmin", password="pw")
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
        )

    def test_checkbox_displayed_on_change_form(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        self.assertContains(response, "name=\"_user_datum\"")
        self.assertContains(response, "User Datum")

    def test_userdatum_created_when_checked(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        data = {
            "user": self.user.pk,
            "host": "http://test",
            "database": "db",
            "username": "odoo",
            "password": "",
            "_user_datum": "on",
            "_save": "Save",
        }
        self.client.post(url, data)
        ct = ContentType.objects.get_for_model(OdooProfile)
        self.assertTrue(
            UserDatum.objects.filter(
                user=self.user, content_type=ct, object_id=self.profile.pk
            ).exists()
        )
