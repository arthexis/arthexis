from pathlib import Path

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.conf import settings
from django.core.management import call_command
from django.contrib.messages import get_messages

from teams.models import OdooProfile

from awg.models import CalculatorTemplate

from core.models import OdooProfile as CoreOdooProfile, Todo
from core.user_data import dump_user_fixture, load_user_fixtures


class UserDataAdminTests(TransactionTestCase):
    def setUp(self):
        call_command("flush", verbosity=0, interactive=False)
        User = get_user_model()
        self.user = User.objects.create_superuser("udadmin", password="pw")
        self.client.login(username="udadmin", password="pw")
        data_root = Path(self.user.data_path or Path(settings.BASE_DIR) / "data")
        data_root.mkdir(exist_ok=True)
        for f in data_root.glob("*.json"):
            f.unlink()
        self.data_dir = data_root
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
        )
        self.fixture_path = self.data_dir / f"teams_odooprofile_{self.profile.pk}.json"

    def tearDown(self):
        for path in self.data_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        call_command("flush", verbosity=0, interactive=False)

    def test_userdatum_checkbox(self):
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="_user_datum"')

    def test_save_user_datum_creates_fixture(self):
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
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
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_user_data)
        self.assertTrue(self.fixture_path.exists())
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any(str(self.fixture_path) in msg for msg in messages))

    def test_unchecking_removes_fixture(self):
        self.profile.is_user_data = True
        self.profile.save()
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        data = {
            "user": self.user.pk,
            "host": "http://test",
            "database": "db",
            "username": "odoo",
            "password": "",
            "_save": "Save",
        }
        self.client.post(url, data)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_user_data)
        self.assertFalse(self.fixture_path.exists())

    def test_load_user_fixture_marks_user_data_flag(self):
        core_profile = CoreOdooProfile.objects.get(pk=self.profile.pk)
        todo = Todo.objects.create(request="Test TODO")
        calculator = CalculatorTemplate.objects.create(name="Test Template")

        for instance in (core_profile, todo, calculator):
            with self.subTest(model=instance._meta.label_lower):
                path = self.data_dir / (
                    f"{instance._meta.app_label}_{instance._meta.model_name}_{instance.pk}.json"
                )
                type(instance).all_objects.filter(pk=instance.pk).update(
                    is_user_data=True
                )
                instance.refresh_from_db()
                dump_user_fixture(instance, self.user)
                self.assertTrue(path.exists())
                type(instance).all_objects.filter(pk=instance.pk).update(
                    is_user_data=False
                )
                instance.refresh_from_db()
                self.assertFalse(instance.is_user_data)
                load_user_fixtures(self.user)
                instance.refresh_from_db()
                self.assertTrue(instance.is_user_data)
