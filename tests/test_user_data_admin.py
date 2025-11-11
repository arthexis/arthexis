import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import quote

from django import forms
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.auth.models import Permission
from django.test import TransactionTestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.conf import settings
from django.core.management import call_command
from django.test import RequestFactory, TransactionTestCase
from django.urls import reverse

from teams.models import OdooProfile

from teams.models import EmailOutbox

from awg.models import CalculatorTemplate

from core.models import (
    OdooProfile as CoreOdooProfile,
    ReleaseManager as CoreReleaseManager,
    Todo,
)
from core.user_data import dump_user_fixture, load_user_fixtures, _resolve_fixture_user


class UserDataAdminTests(TransactionTestCase):
    def setUp(self):
        call_command("flush", verbosity=0, interactive=False)
        User = get_user_model()
        self.user = User.objects.create_superuser("udadmin", password="pw")
        self.client.login(username="udadmin", password="pw")
        data_root = Path(self.user.data_path or Path(settings.BASE_DIR) / "data")
        data_root.mkdir(exist_ok=True)
        user_dir = data_root / self.user.username
        user_dir.mkdir(exist_ok=True)
        for f in user_dir.glob("*.json"):
            f.unlink()
        self.data_root = data_root
        self.data_dir = user_dir
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
        )
        self.fixture_path = self.data_dir / f"core_odooprofile_{self.profile.pk}.json"

    def tearDown(self):
        for path in self.data_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        call_command("flush", verbosity=0, interactive=False)

    def test_userdatum_checkbox(self):
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="_user_datum"')

    def test_admin_account_shows_userdatum_checkbox(self):
        User = get_user_model()
        admin_user = User.objects.create_superuser(User.ADMIN_USERNAME, password="pw")
        self.client.logout()
        self.client.login(username=admin_user.username, password="pw")
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        self.assertContains(response, 'name="_user_datum"')

    def test_user_change_view_hides_global_user_datum_checkbox(self):
        UserModel = get_user_model()
        admin_model = None
        for model in admin.site._registry:
            if model._meta.concrete_model is UserModel:
                admin_model = model
                break
        self.assertIsNotNone(admin_model)
        url = reverse(
            f"admin:{admin_model._meta.app_label}_{admin_model._meta.model_name}_change",
            args=[self.user.pk],
        )
        response = self.client.get(url)
        self.assertNotContains(response, 'name="_user_datum"')

    def test_save_user_datum_creates_fixture(self):
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        data = {
            "user": self.user.pk,
            "crm": self.profile.crm,
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

    def test_user_datum_fixture_preserves_password(self):
        self.profile.is_user_data = True
        self.profile.save(update_fields=["is_user_data"])
        dump_user_fixture(self.profile, self.user)
        self.assertTrue(self.fixture_path.exists())

        data = json.loads(self.fixture_path.read_text())
        self.assertTrue(data)
        self.assertEqual(data[0]["fields"]["password"], "secret")

        CoreOdooProfile.all_objects.filter(pk=self.profile.pk).delete()
        self.assertFalse(CoreOdooProfile.all_objects.filter(pk=self.profile.pk).exists())

        load_user_fixtures(self.user)
        reloaded = CoreOdooProfile.objects.get(pk=self.profile.pk)
        self.assertEqual(reloaded.password, "secret")
        self.assertTrue(reloaded.is_user_data)

    def test_unchecking_removes_fixture(self):
        self.profile.is_user_data = True
        self.profile.save()
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        data = {
            "user": self.user.pk,
            "crm": self.profile.crm,
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

    def test_change_list_includes_user_data_star(self):
        url = reverse("admin:teams_odooprofile_changelist")
        response = self.client.get(url)
        self.assertContains(response, 'class="column-user-data user-data-column"')
        self.assertContains(response, 'favorite-star user-data-star')
        toggle_url = reverse(
            "admin:user_data_toggle",
            args=("teams", "odooprofile", self.profile.pk),
        )
        self.assertContains(response, f'formaction="{toggle_url}"')
        self.assertContains(response, 'formmethod="post"')

    def test_toggle_user_data_star(self):
        toggle_url = reverse(
            "admin:user_data_toggle",
            args=("teams", "odooprofile", self.profile.pk),
        )
        changelist_url = reverse("admin:teams_odooprofile_changelist")
        response = self.client.post(
            f"{toggle_url}?next={quote(changelist_url, safe='')}",
            follow=True,
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_user_data)
        self.assertTrue(self.fixture_path.exists())
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("User datum saved" in msg for msg in messages))

        response = self.client.post(
            f"{toggle_url}?next={quote(changelist_url, safe='')}",
            follow=True,
        )
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_user_data)
        self.assertFalse(self.fixture_path.exists())
        messages = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("User datum removed" in msg for msg in messages))

    def test_toggle_user_data_requires_change_permission(self):
        User = get_user_model()
        viewer = User.objects.create_user(
            "viewer", password="pw", is_staff=True, is_superuser=False
        )
        view_permission = Permission.objects.get(
            codename="view_odooprofile", content_type__app_label="teams"
        )
        viewer.user_permissions.add(view_permission)

        self.client.logout()
        logged_in = self.client.login(username="viewer", password="pw")
        self.assertTrue(logged_in)

        toggle_url = reverse(
            "admin:user_data_toggle",
            args=("teams", "odooprofile", self.profile.pk),
        )
        changelist_url = reverse("admin:teams_odooprofile_changelist")

        response = self.client.post(toggle_url, {"next": changelist_url})
        self.assertEqual(response.status_code, 403)

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_user_data)

    def test_user_user_data_fixture_includes_profiles(self):
        release_manager = CoreReleaseManager.objects.create(user=self.user)
        core_profile = CoreOdooProfile.objects.get(pk=self.profile.pk)
        UserModel = get_user_model()
        admin_model = None
        user_admin = None
        for model, admin_instance in admin.site._registry.items():
            if model._meta.concrete_model is UserModel:
                admin_model = model
                user_admin = admin_instance
                break
        self.assertIsNotNone(user_admin)
        admin_user = admin_model.objects.get(pk=self.user.pk)
        rf = RequestFactory()
        request = rf.post("/", {})
        request.user = self.user
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        class SimpleUserForm(forms.ModelForm):
            class Meta:
                model = admin_model
                fields = ["username"]

        form = SimpleUserForm({"username": admin_user.username}, instance=admin_user)
        self.assertTrue(form.is_valid())

        user_admin.save_model(request, admin_user, form, True)

        class DummyInlineForm:
            def __init__(self, instance, cleaned_data):
                self.instance = instance
                self.cleaned_data = cleaned_data

        class DummyFormset:
            def __init__(self, forms):
                self.forms = forms
                self.deleted_objects = []

            def save(self):
                return self.forms

        def run_formset(instance):
            formset = DummyFormset(
                [DummyInlineForm(instance, {"user_datum": True, "DELETE": False})]
            )
            user_admin.save_formset(request, form, formset, True)

        run_formset(core_profile)
        run_formset(release_manager)

        expected_paths = [
            self.data_dir / f"core_odooprofile_{core_profile.pk}.json",
            self.data_dir / f"core_releasemanager_{release_manager.pk}.json",
        ]
        for path in expected_paths:
            with self.subTest(path=path.name):
                self.assertTrue(path.exists())

        user_fixture = self.data_dir / f"core_user_{self.user.pk}.json"
        self.assertFalse(user_fixture.exists())

        core_user = UserModel.objects.get(pk=self.user.pk)
        profile_instances = [core_profile, release_manager]
        for instance in [core_user] + profile_instances:
            with self.subTest(instance=instance._meta.label_lower):
                type(instance).all_objects.filter(pk=instance.pk).update(
                    is_user_data=False
                )
                instance.refresh_from_db()
                self.assertFalse(instance.is_user_data)

        load_user_fixtures(self.user)

        for instance in profile_instances:
            with self.subTest(reloaded=instance._meta.label_lower):
                instance.refresh_from_db()
                self.assertTrue(instance.is_user_data)

        core_user.refresh_from_db()
        self.assertFalse(core_user.is_user_data)

    def test_user_userdatum_checkbox_ignored(self):
        UserModel = get_user_model()
        admin_model = None
        user_admin = None
        for model, admin_instance in admin.site._registry.items():
            if model._meta.concrete_model is UserModel:
                admin_model = model
                user_admin = admin_instance
                break
        self.assertIsNotNone(user_admin)
        admin_user = admin_model.objects.get(pk=self.user.pk)
        rf = RequestFactory()

        class SimpleUserForm(forms.ModelForm):
            class Meta:
                model = admin_model
                fields = ["username"]

        user_fixture = (
            self.data_dir
            / f"{admin_model._meta.app_label}_{admin_model._meta.model_name}_{admin_user.pk}.json"
        )

        request = rf.post("/", {"_user_datum": "on"})
        request.user = self.user
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))
        form = SimpleUserForm({"username": admin_user.username}, instance=admin_user)
        self.assertTrue(form.is_valid())
        user_admin.save_model(request, admin_user, form, True)
        admin_user.refresh_from_db()
        self.assertFalse(admin_user.is_user_data)
        self.assertFalse(user_fixture.exists())

        request = rf.post("/", {})
        request.user = self.user
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))
        form = SimpleUserForm({"username": admin_user.username}, instance=admin_user)
        self.assertTrue(form.is_valid())
        user_admin.save_model(request, admin_user, form, True)
        admin_user.refresh_from_db()
        self.assertFalse(admin_user.is_user_data)
        self.assertFalse(user_fixture.exists())

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

    def test_load_user_fixture_skips_empty_files(self):
        empty = self.data_dir / "core_todo_1.json"
        empty.write_text("[]", encoding="utf-8")

        with patch("core.user_data.call_command") as mock_call:
            load_user_fixtures(self.user)

        self.assertFalse(empty.exists())
        mock_call.assert_not_called()

    def test_load_user_fixture_skips_outdated_app(self):
        outdated = self.data_dir / "missing_app_widget_1.json"
        fixture = [
            {
                "model": "missing_app.widget",
                "pk": "widget-1",
                "fields": {"name": "Legacy Widget"},
            }
        ]
        outdated.write_text(json.dumps(fixture), encoding="utf-8")

        with patch("core.user_data.call_command") as mock_call:
            load_user_fixtures(self.user)

        self.assertTrue(outdated.exists())
        mock_call.assert_not_called()

    def test_user_data_view_deduplicates_proxy_models(self):
        outbox = EmailOutbox.objects.create(
            user=self.user,
            host="smtp.example.com",
            username="mailer",
        )
        EmailOutbox.all_objects.filter(pk=outbox.pk).update(is_user_data=True)
        outbox.refresh_from_db()

        response = self.client.get(reverse("admin:user_data"))
        self.assertEqual(response.status_code, 200)

        sections = response.context_data["sections"]
        email_sections = [
            section
            for section in sections
            if section["opts"].model_name == "emailoutbox"
        ]

        self.assertEqual(len(email_sections), 1)
        self.assertEqual(len(email_sections[0]["items"]), 1)
        self.assertIn(str(outbox), email_sections[0]["items"][0]["label"])

    def test_admin_fixtures_delegate_to_system_user(self):
        User = get_user_model()
        User.all_objects.filter(username=User.ADMIN_USERNAME).delete()
        system_user, _ = User.all_objects.get_or_create(
            username=User.SYSTEM_USERNAME,
            defaults={
                "email": "arthexis@example.com",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        changed = False
        if not system_user.is_staff:
            system_user.is_staff = True
            changed = True
        if not system_user.is_superuser:
            system_user.is_superuser = True
            changed = True
        if not system_user.has_usable_password():
            system_user.set_password("pw")
            changed = True
        if changed:
            system_user.save()

        admin_user = User.objects.create_superuser(User.ADMIN_USERNAME, password="pw")
        admin_user.operate_as = system_user
        admin_user.save(update_fields=["operate_as"])

        with TemporaryDirectory() as temp_dir:
            system_user.data_path = temp_dir
            system_user.save(update_fields=["data_path"])
            todo = Todo.objects.create(request="Delegate fixture")
            Todo.all_objects.filter(pk=todo.pk).update(is_user_data=True)
            todo.refresh_from_db()

            target_user = _resolve_fixture_user(todo, admin_user)
            self.assertEqual(target_user, system_user)

            dump_user_fixture(todo, target_user)
            expected_path = (
                Path(temp_dir)
                / system_user.username
                / f"{todo._meta.app_label}_{todo._meta.model_name}_{todo.pk}.json"
            )
            self.assertTrue(expected_path.exists())
