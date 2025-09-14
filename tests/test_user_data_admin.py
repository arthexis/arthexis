from pathlib import Path

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.conf import settings
from django.core.management import call_command
from django.contrib.messages import get_messages

from teams.models import OdooProfile


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
        self.fixture_path.unlink(missing_ok=True)
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
