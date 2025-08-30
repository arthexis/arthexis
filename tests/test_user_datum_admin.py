from pathlib import Path

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.management import call_command
from django.contrib.messages import get_messages

import importlib.util

spec = importlib.util.spec_from_file_location(
    "env_refresh", Path(__file__).resolve().parent.parent / "env-refresh.py"
)
env_refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env_refresh)
run_database_tasks = env_refresh.run_database_tasks

from core.models import OdooProfile
from core.user_data import UserDatum


class UserDatumAdminTests(TestCase):
    def setUp(self):
        call_command("flush", verbosity=0, interactive=False)
        User = get_user_model()
        self.user = User.objects.create_superuser("udadmin", password="pw")
        self.client.login(username="udadmin", password="pw")
        self.data_dir = Path(settings.BASE_DIR) / "data"
        for f in self.data_dir.glob("*.json"):
            f.unlink()
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
        )
        self.fixture_path = (
            self.data_dir
            / f"{self.user.pk}_core_odooprofile_{self.profile.pk}.json"
        )

    def tearDown(self):
        self.fixture_path.unlink(missing_ok=True)

    def test_checkbox_displayed_on_change_form(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        self.assertContains(response, "name=\"_user_datum\"")
        self.assertContains(response, "User Datum")

    def test_checkbox_has_form_attribute(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        form_id = f"{self.profile._meta.model_name}_form"
        self.assertContains(
            response, f'name="_user_datum" form="{form_id}"'
        )

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
        response = self.client.post(url, data, follow=True)
        ct = ContentType.objects.get_for_model(OdooProfile)
        self.assertTrue(
            UserDatum.objects.filter(
                user=self.user, content_type=ct, object_id=self.profile.pk
            ).exists()
        )
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(
            any(str(self.fixture_path) in msg for msg in messages),
        )


    def test_userdatum_persists_after_save(self):
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
        response = self.client.get(url)
        form_id = f"{self.profile._meta.model_name}_form"
        self.assertContains(
            response, f'name="_user_datum" form="{form_id}" checked'
        )
        
    def test_fixture_created_and_loaded_on_env_refresh(self):
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
        self.assertTrue(self.fixture_path.exists())

        call_command("flush", verbosity=0, interactive=False)
        run_database_tasks()

        ct = ContentType.objects.get_for_model(OdooProfile)
        self.assertTrue(
            UserDatum.objects.filter(
                user_id=self.user.pk, content_type=ct, object_id=self.profile.pk
            ).exists()
        )
