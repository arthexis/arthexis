from pathlib import Path
from io import BytesIO
from zipfile import ZipFile

import json
import os
import shutil
import pytest
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages import get_messages

import importlib.util

spec = importlib.util.spec_from_file_location(
    "env_refresh", Path(__file__).resolve().parent.parent / "env-refresh.py"
)
env_refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env_refresh)
run_database_tasks = env_refresh.run_database_tasks

from core.models import Address, EnergyAccount, OdooProfile, SecurityGroup
from core.user_data import UserDatum


class UserDatumAdminTests(TransactionTestCase):
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
            self.data_dir / f"{self.user.pk}_core_odooprofile_{self.profile.pk}.json"
        )

    def tearDown(self):
        self.fixture_path.unlink(missing_ok=True)
        call_command("flush", verbosity=0, interactive=False)
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()

    def test_checkbox_displayed_on_change_form(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="_user_datum"')
        self.assertContains(response, "User Datum")

    def test_checkbox_has_form_attribute(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        form_id = f"{self.profile._meta.model_name}_form"
        self.assertContains(response, f'name="_user_datum" form="{form_id}"')

    def test_checkbox_not_displayed_for_non_entity(self):
        group = SecurityGroup.objects.create(name="Temp")
        url = reverse(
            "admin:post_office_workgroupsecuritygroup_change", args=[group.pk]
        )
        response = self.client.get(url)
        self.assertNotContains(response, 'name="_user_datum"')

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
        self.assertContains(response, f'name="_user_datum" form="{form_id}" checked')

    @pytest.mark.skip("fixture path handling pending update")
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

    def test_load_user_fixtures_command(self):
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
        call_command("load_user_fixtures")

        ct = ContentType.objects.get_for_model(OdooProfile)
        self.assertTrue(
            UserDatum.objects.filter(
                user_id=self.user.pk, content_type=ct, object_id=self.profile.pk
            ).exists()
        )

    def test_env_refresh_creates_userdatum_for_fixture(self):
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

        base_dir = Path(settings.BASE_DIR)
        tmp_dir = base_dir / "temp_ud_fixture"
        fixture_dir = tmp_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        user_fixture = fixture_dir / "users__sdadmin.json"
        call_command(
            "dumpdata",
            f"{self.user._meta.app_label}.{self.user._meta.model_name}",
            pks=str(self.user.pk),
            output=str(user_fixture),
        )
        profile_fixture = fixture_dir / self.fixture_path.name
        profile_fixture.write_text(self.fixture_path.read_text())
        self.fixture_path.unlink()

        rel_user = str(user_fixture.relative_to(base_dir))
        rel_profile = str(profile_fixture.relative_to(base_dir))

        env_refresh._fixture_files = lambda: [rel_user, rel_profile]
        from django.core.management import call_command as django_call

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                django_call(name, *args, **kwargs)

        env_refresh.call_command = fake_call_command

        call_command("flush", verbosity=0, interactive=False)
        env_refresh.run_database_tasks()

        ct = ContentType.objects.get_for_model(OdooProfile)
        self.assertTrue(
            UserDatum.objects.filter(
                user_id=self.user.pk, content_type=ct, object_id=self.profile.pk
            ).exists()
        )

        shutil.rmtree(tmp_dir)

    def test_copy_unmarks_user_datum(self):
        address = Address.objects.create(
            street="Main",
            number="1",
            municipality="Saltillo",
            state="CO",
            postal_code="25000",
        )
        url = reverse("admin:core_address_change", args=[address.pk])
        data = {
            "street": address.street,
            "number": address.number,
            "municipality": address.municipality,
            "state": address.state,
            "postal_code": address.postal_code,
            "_user_datum": "on",
            "_save": "Save",
        }
        self.client.post(url, data)
        ct = ContentType.objects.get_for_model(Address)
        self.assertTrue(
            UserDatum.objects.filter(
                user=self.user, content_type=ct, object_id=address.pk
            ).exists()
        )
        copy_data = data | {"_saveacopy": "Save as a copy"}
        self.client.post(url, copy_data)
        self.assertEqual(Address.objects.count(), 2)
        new_addr = Address.objects.order_by("-pk").first()
        self.assertFalse(
            UserDatum.objects.filter(
                user=self.user, content_type=ct, object_id=new_addr.pk
            ).exists()
        )

    def test_energyaccount_checkbox_displayed_on_change_form(self):
        account = EnergyAccount.objects.create(name="MAIN")
        url = reverse("admin:core_energyaccount_change", args=[account.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="_user_datum"')
        self.assertContains(response, "User Datum")

    def test_fixture_updates_on_subsequent_saves(self):
        """Fixture content reflects changes after additional saves."""
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        data = {
            "user": self.user.pk,
            "host": "http://initial",
            "database": "db",
            "username": "odoo",
            "password": "",
            "_user_datum": "on",
            "_save": "Save",
        }
        self.client.post(url, data)
        self.assertIn("http://initial", self.fixture_path.read_text())

        update = data | {"host": "http://updated"}
        self.client.post(url, update)
        self.assertIn("http://updated", self.fixture_path.read_text())

    def test_unchecking_deletes_fixture(self):
        """Removing the user datum flag deletes link and fixture."""
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

        unchecked = data.copy()
        unchecked.pop("_user_datum")
        self.client.post(url, unchecked)
        self.assertFalse(self.fixture_path.exists())
        ct = ContentType.objects.get_for_model(OdooProfile)
        self.assertFalse(
            UserDatum.objects.filter(
                user=self.user, content_type=ct, object_id=self.profile.pk
            ).exists()
        )


class UserDataViewTests(TestCase):
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
        ct = ContentType.objects.get_for_model(OdooProfile)
        UserDatum.objects.create(
            user=self.user, content_type=ct, object_id=self.profile.pk
        )
        self.fixture_path = (
            self.data_dir / f"{self.user.pk}_core_odooprofile_{self.profile.pk}.json"
        )

    def test_user_data_view_lists_items(self):
        url = reverse("admin:user_data")
        response = self.client.get(url)
        self.assertContains(response, str(self.profile))
        self.assertContains(response, self.fixture_path.name)

    def test_admin_index_shows_buttons(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, reverse("admin:seed_data"))
        self.assertContains(response, reverse("admin:user_data"))
        self.assertContains(response, reverse("admin:system"))
        self.assertContains(response, reverse("admin:environment"))
        self.assertContains(response, reverse("admin:sigil_builder"))

    def test_system_page_loads(self):
        response = self.client.get(reverse("admin:system"))
        self.assertContains(response, "Hostname")

    def test_shell_command_not_listed(self):
        response = self.client.get(reverse("admin:system"))
        self.assertNotContains(
            response, reverse("admin:system_command", args=["shell"])
        )

    def test_environment_page_loads(self):
        response = self.client.get(reverse("admin:environment"))
        self.assertContains(response, "Environment Variables")
        self.assertContains(response, "Django Settings")
        self.assertContains(response, "PATH")
        self.assertContains(response, "DEBUG")

    def test_sigil_builder_page_loads(self):
        response = self.client.get(reverse("admin:sigil_builder"))
        self.assertContains(response, "Sigil Builder")

    def test_sigil_builder_resolves_sigil_without_brackets(self):
        os.environ["FOO"] = "BAR"
        try:
            response = self.client.post(
                reverse("admin:sigil_builder"), {"sigil": "ENV.FOO"}
            )
        finally:
            del os.environ["FOO"]
        self.assertContains(response, "BAR")

    def test_user_data_page_has_import_export_links(self):
        response = self.client.get(reverse("admin:user_data"))
        self.assertContains(response, reverse("admin:user_data_export"))
        self.assertContains(response, reverse("admin:user_data_import"))
        self.assertContains(response, 'type="file"')

    def test_export_and_import_roundtrip(self):
        export_url = reverse("admin:user_data_export")
        response = self.client.get(export_url)
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.content)) as zf:
            self.assertIn(self.fixture_path.name, zf.namelist())

        profile_pk = self.profile.pk
        ct = ContentType.objects.get_for_model(OdooProfile)
        UserDatum.objects.all().delete()
        self.profile.delete()
        self.fixture_path.unlink(missing_ok=True)

        upload = SimpleUploadedFile(
            "user_data.zip", response.content, content_type="application/zip"
        )
        self.client.post(
            reverse("admin:user_data_import"), {"data_zip": upload}, follow=True
        )
        self.assertTrue(OdooProfile.objects.filter(pk=profile_pk).exists())
        self.assertTrue(
            UserDatum.objects.filter(
                user=self.user, content_type=ct, object_id=profile_pk
            ).exists()
        )
