import os
import sys
import json
import shutil
import importlib.util
from glob import glob
import io
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase, TransactionTestCase
from django.db import connection, connections
from django.conf import settings
from nodes.models import Node, NodeRole
from teams.models import OdooProfile, SecurityGroup
from django.contrib.sites.models import Site
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.utils import timezone
import socket
from core.models import Todo
from ocpp.models import Brand, WMICode
from core import user_data
from core.user_data import dump_user_fixture
from pages.models import Application, Module, Landing


class SeedDataEntityTests(TestCase):
    def test_preserve_seed_data_on_create(self):
        role = NodeRole.objects.create(name="Tester", is_seed_data=True)
        self.assertTrue(NodeRole.all_objects.get(pk=role.pk).is_seed_data)


class FixtureReloadTests(TestCase):
    def test_reloading_unique_fixture_updates_existing(self):
        fixtures = [
            *sorted(glob("ocpp/fixtures/ev_brands__*.json")),
            *sorted(glob("ocpp/fixtures/ev_models__*.json")),
        ]
        call_command("loaddata", *fixtures, verbosity=0)
        code = WMICode.objects.get(code="TRU")
        code.brand = Brand.objects.get(name="Porsche")
        code.save()
        call_command("loaddata", *fixtures, verbosity=0)
        code.refresh_from_db()
        self.assertEqual(code.brand.name, "Audi")


class TodoFixtureDateTests(TestCase):
    def test_core_todo_fixtures_define_created_on(self):
        base_dir = Path(settings.BASE_DIR) / "core" / "fixtures"
        fixtures = sorted(base_dir.glob("todo__*.json"))
        self.assertFalse(
            fixtures,
            "Legacy TODO fixtures should not ship with the project",
        )


class SiteFixtureTests(TestCase):
    def test_core_site_fixtures_cover_expected_domains(self):
        base_dir = Path(settings.BASE_DIR)
        expected_domains = {
            "arthexis.com": "Arthexis",
            "127.0.0.1": "Local",
            "localhost": "Local",
            "10.42.0.1": "Router",
            "192.168.129.10": "Gateway",
        }
        fixtures = {}
        for path in sorted(
            (base_dir / "core" / "fixtures").glob("references__00_site_*.json")
        ):
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(data, list):
                continue
            for obj in data:
                if not isinstance(obj, dict):
                    continue
                if obj.get("model") != "sites.site":
                    continue
                fields = obj.get("fields", {})
                domain = fields.get("domain")
                name = fields.get("name")
                if domain:
                    fixtures[domain] = name

        found = {domain: fixtures.get(domain) for domain in expected_domains}
        self.assertEqual(found, expected_domains)


class EntityInheritanceTests(TestCase):
    def test_local_models_inherit_entity(self):
        from django.apps import apps
        from core.entity import Entity

        allowed = {
            "core.SecurityGroup",
            "core.TOTPDeviceSettings",
            "ocpp.DataTransferMessage",
            "ocpp.ChargerConfiguration",
            "nodes.PendingNetMessage",
            "pages.Manual",
            "pages.SiteProxy",
            "teams.SecurityGroup",
            "teams.TOTPDevice",
        }
        for app_label in getattr(settings, "LOCAL_APPS", []):
            config = apps.get_app_config(app_label)
            for model in config.get_models():
                label = model._meta.label
                if label in allowed:
                    continue
                self.assertTrue(issubclass(model, Entity), label)


class SeedDataAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("sdadmin", password="pw")
        self.client.login(username="sdadmin", password="pw")
        self.profile = OdooProfile.objects.create(
            user=self.user,
            host="http://test",
            database="db",
            username="odoo",
            password="secret",
        )

    def tearDown(self):
        User = get_user_model()
        User.all_objects.filter(username="admin").delete()

    def test_admin_index_seed_data_button(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Seed Data")
        self.assertNotContains(response, "Seed Datum")

    def test_checkbox_displayed_on_change_form(self):
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn('name="_seed_datum"', content)
        self.assertIn("Seed Datum", content)
        self.assertIn('name="_user_datum"', content)
        self.assertIn("User Datum", content)
        self.assertIn(f'[ <a href="{reverse("admin:user_data")}">View</a> ]', content)
        self.assertIn(f'[ <a href="{reverse("admin:seed_data")}">View</a> ]', content)
        self.assertLess(
            content.index('name="_user_datum"'),
            content.index('name="_seed_datum"'),
        )

    def test_checkbox_has_form_attribute(self):
        url = reverse("admin:teams_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        form_id = f"{self.profile._meta.model_name}_form"
        self.assertContains(response, f'name="_seed_datum" form="{form_id}"')

    def test_checkbox_not_displayed_for_non_entity(self):
        group = SecurityGroup.objects.create(name="Temp")
        url = reverse("admin:teams_securitygroup_change", args=[group.pk])
        response = self.client.get(url)
        self.assertNotContains(response, 'name="_seed_datum"')
        self.assertNotContains(response, 'name="_user_datum"')
        from django.contrib import admin
        from core.user_data import EntityModelAdmin

        self.assertNotIsInstance(admin.site._registry[SecurityGroup], EntityModelAdmin)

    def test_entity_admins_auto_patched(self):
        from django.contrib import admin
        from core.entity import Entity
        from core.user_data import EntityModelAdmin

        for model, model_admin in admin.site._registry.items():
            if issubclass(model, Entity):
                self.assertIsInstance(model_admin, EntityModelAdmin)

    def test_seed_datum_persists_after_save(self):
        OdooProfile.all_objects.filter(pk=self.profile.pk).update(is_seed_data=True)
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
        profile = OdooProfile.all_objects.get(pk=self.profile.pk)
        self.assertTrue(profile.is_seed_data)
        response = self.client.get(url)
        form_id = f"{self.profile._meta.model_name}_form"
        self.assertContains(
            response,
            f'name="_seed_datum" form="{form_id}" checked disabled',
        )


class EnvRefreshFixtureTests(TestCase):
    def setUp(self):
        pass

    def test_env_refresh_marks_seed_data(self):
        base_dir = Path(settings.BASE_DIR)
        tmp_dir = base_dir / "temp_fixture"
        fixture_dir = tmp_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixture_dir / "sample.json"
        fixture_path.write_text(
            json.dumps(
                [
                    {
                        "model": "nodes.noderole",
                        "pk": 999,
                        "fields": {"name": "Fixture Role"},
                    },
                    {
                        "model": "auth.group",
                        "pk": 777,
                        "fields": {"name": "Fixture Group"},
                    },
                ]
            )
        )
        rel_path = str(fixture_path.relative_to(base_dir))
        spec = importlib.util.spec_from_file_location(
            "env_refresh", base_dir / "env-refresh.py"
        )
        env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env_refresh)
        env_refresh._fixture_files = lambda: [rel_path]
        from django.core.management import call_command as django_call

        loaded_fixtures: list[str] = []

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                loaded_fixtures.extend(args)
                django_call(name, *args, **kwargs)
            # ignore other commands

        env_refresh.call_command = fake_call_command
        buf = io.StringIO()
        with redirect_stdout(buf):
            env_refresh.run_database_tasks()
        output = buf.getvalue()
        role = NodeRole.all_objects.get(pk=999)
        self.assertTrue(role.is_seed_data)
        group = Group.objects.get(pk=777)
        self.assertFalse(hasattr(group, "is_seed_data"))
        self.assertIn(".", output)
        self.assertIn("nodes.NodeRole: 1", output)
        self.assertEqual(len(loaded_fixtures), 1)
        self.assertNotEqual(loaded_fixtures[0], str(fixture_path))
        shutil.rmtree(tmp_dir)

    def test_env_refresh_passthrough_fixture_without_changes(self):
        base_dir = Path(settings.BASE_DIR)
        tmp_dir = base_dir / "temp_fixture_passthrough"
        fixture_dir = tmp_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixture_dir / "sample_passthrough.json"
        fixture_path.write_text(
            json.dumps(
                [
                    {
                        "model": "nodes.noderole",
                        "pk": 1000,
                        "fields": {"name": "Passthrough Role", "is_seed_data": True},
                    }
                ]
            )
        )
        rel_path = str(fixture_path.relative_to(base_dir))
        spec = importlib.util.spec_from_file_location(
            "env_refresh", base_dir / "env-refresh.py"
        )
        env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env_refresh)
        env_refresh._fixture_files = lambda: [rel_path]
        from django.core.management import call_command as django_call

        loaded_fixtures: list[str] = []

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                loaded_fixtures.extend(args)
                django_call(name, *args, **kwargs)
            # ignore other commands

        env_refresh.call_command = fake_call_command
        buf = io.StringIO()
        with redirect_stdout(buf):
            env_refresh.run_database_tasks()
        role = NodeRole.all_objects.get(pk=1000)
        self.assertTrue(role.is_seed_data)
        self.assertEqual(len(loaded_fixtures), 1)
        self.assertEqual(loaded_fixtures[0], str(fixture_path))
        shutil.rmtree(tmp_dir)

    def test_env_refresh_preserves_non_fixture_sites(self):
        base_dir = Path(settings.BASE_DIR)
        spec = importlib.util.spec_from_file_location(
            "env_refresh_sites", base_dir / "env-refresh.py"
        )
        env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env_refresh)

        site_fixtures = [
            str(path.relative_to(base_dir))
            for path in (base_dir / "core" / "fixtures").glob(
                "references__00_site_*.json"
            )
        ]
        env_refresh._fixture_files = lambda: site_fixtures

        loaded_fixtures: list[str] = []

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                loaded_fixtures.extend(args)
            return None

        env_refresh.call_command = fake_call_command

        Site.objects.all().delete()
        Site.objects.create(domain="127.0.0.1", name="Old Local")
        Site.objects.create(domain="operator.example", name="Operator Portal")

        env_refresh.run_database_tasks()

        self.assertIn("operator.example", Site.objects.values_list("domain", flat=True))
        self.assertEqual(
            Site.objects.get(domain="operator.example").name, "Operator Portal"
        )

        Site.objects.clear_cache()
        current_site = Site.objects.get_current()
        self.assertEqual(current_site.domain, "127.0.0.1")
        self.assertEqual(
            Site.objects.filter(domain=current_site.domain).count(),
            1,
        )

        expected_sites = {
            "arthexis.com": "Arthexis",
            "127.0.0.1": "Local",
            "localhost": "Local",
            "10.42.0.1": "Router",
            "192.168.129.10": "Gateway",
        }
        for domain, name in expected_sites.items():
            site = Site.objects.get(domain=domain)
            self.assertEqual(site.name, name)

        self.assertEqual(len(loaded_fixtures), 0)
        self.assertEqual(
            Site.objects.count(),
            len(expected_sites) + 1,  # fixture domains plus custom site
        )


class EnvRefreshNodeTests(TestCase):
    def setUp(self):
        base_dir = Path(settings.BASE_DIR)
        spec = importlib.util.spec_from_file_location(
            "env_refresh", base_dir / "env-refresh.py"
        )
        self.env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.env_refresh)
        self.env_refresh.call_command = lambda *args, **kwargs: None
        self.env_refresh._fixture_files = lambda: []

    def test_env_refresh_registers_node(self):
        Node.objects.all().delete()
        self.env_refresh.run_database_tasks()
        self.assertIsNotNone(Node.get_local())

    def test_env_refresh_updates_existing_node(self):
        mac = Node.get_current_mac()
        Node.objects.create(hostname="old", address="0.0.0.0", port=1, mac_address=mac)
        self.env_refresh.run_database_tasks()
        node = Node.objects.get(mac_address=mac)
        self.assertEqual(node.hostname, socket.gethostname())

    def test_env_refresh_creates_control_site(self):
        Node.objects.all().delete()
        Site.objects.all().delete()
        lock_dir = Path(settings.BASE_DIR) / "locks"
        lock_dir.mkdir(exist_ok=True)
        control_lock = lock_dir / "control.lck"
        try:
            control_lock.touch()
            self.env_refresh.run_database_tasks()
            node = Node.get_local()
            self.assertIsNotNone(node)
            self.assertTrue(
                Site.objects.filter(
                    domain=node.public_endpoint, name="Control"
                ).exists()
            )
        finally:
            control_lock.unlink(missing_ok=True)


class EnvRefreshLandingTests(TestCase):
    def setUp(self):
        base_dir = Path(settings.BASE_DIR)
        spec = importlib.util.spec_from_file_location(
            "env_refresh_landings", base_dir / "env-refresh.py"
        )
        self.env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.env_refresh)
        self.env_refresh._fixture_files = lambda: []
        self.env_refresh.call_command = lambda *args, **kwargs: None

    def test_env_refresh_marks_landings_seed_data_once(self):
        role = NodeRole.objects.create(name=f"LandingRole-{uuid.uuid4().hex}")
        app = Application.objects.create(
            name=f"landing_app_{uuid.uuid4().hex}", description=""
        )
        module = Module.objects.create(
            node_role=role,
            application=app,
            path="/landing-module/",
        )
        landing, _ = Landing.objects.get_or_create(
            module=module,
            path="/landing-module/",
            defaults={"label": "Landing", "description": ""},
        )
        landing.is_seed_data = False
        landing.save(update_fields=["is_seed_data"])

        original_update = self.env_refresh.Landing.objects.update
        update_calls: list[tuple[tuple, dict]] = []

        def tracking_update(*args, **kwargs):
            update_calls.append((args, kwargs))
            return original_update(*args, **kwargs)

        with patch.object(
            self.env_refresh.Landing.objects,
            "update",
            side_effect=tracking_update,
        ):
            self.env_refresh.run_database_tasks()

        landing.refresh_from_db()
        self.assertTrue(landing.is_seed_data)
        self.assertEqual(len(update_calls), 1)


class EnvRefreshTodoFixtureTests(TestCase):
    def setUp(self):
        base_dir = Path(settings.BASE_DIR)
        spec = importlib.util.spec_from_file_location(
            "env_refresh_todos", base_dir / "env-refresh.py"
        )
        self.env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.env_refresh)
        self.base_dir = base_dir
        self.fixture_root = base_dir / f"tmp_env_refresh_{uuid.uuid4().hex}"
        self.fixture_root.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))
        self._commands: list[tuple[str, tuple, dict]] = []
        self._original_call_command = self.env_refresh.call_command

        def _fake_call_command(name, *args, **kwargs):
            self._commands.append((name, args, kwargs))
            return None

        self.env_refresh.call_command = _fake_call_command
        self.addCleanup(self._restore_call_command)
        self._original_fixture_files = self.env_refresh._fixture_files
        self.addCleanup(self._restore_fixture_files)

    def _restore_call_command(self):
        self.env_refresh.call_command = self._original_call_command

    def _restore_fixture_files(self):
        self.env_refresh._fixture_files = self._original_fixture_files

    def test_env_refresh_skips_soft_deleted_todos(self):
        request_text = "Validate screen Example"
        todo = Todo.objects.create(request=request_text, is_seed_data=True)
        todo.done_on = timezone.now()
        todo.save(update_fields=["done_on"])
        todo.delete()
        fixture_path = self.fixture_root / "todos__validate_screen_example.json"
        created_on_value = timezone.now().replace(microsecond=0).isoformat()
        fixture_data = [
            {
                "model": "core.todo",
                "fields": {
                    "request": request_text,
                    "created_on": created_on_value,
                    "url": "/admin/",
                    "request_details": "",
                },
            }
        ]
        fixture_path.write_text(json.dumps(fixture_data, indent=2) + "\n", encoding="utf-8")
        relative = fixture_path.relative_to(self.base_dir)
        self.env_refresh._fixture_files = lambda: [str(relative)]

        self.env_refresh.run_database_tasks()

        self.assertFalse(any(name == "loaddata" for name, _, _ in self._commands))
        self.assertFalse(Todo.objects.filter(request=request_text).exists())
        self.assertTrue(
            Todo.all_objects.filter(request=request_text, is_deleted=True).exists()
        )

    def test_env_refresh_applies_completed_todo_updates(self):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        done_on_value = timezone.now().replace(microsecond=0)
        fixture_path = self.fixture_root / "todo__task_env_refresh_completion.json"
        created_on_value = timezone.now().replace(microsecond=0).isoformat()
        fixture_data = [
            {
                "model": "core.todo",
                "fields": {
                    "request": "Task",
                    "created_on": created_on_value,
                    "url": "",
                    "request_details": "",
                    "done_on": done_on_value.isoformat(),
                    "done_version": "9.9.9",
                    "done_revision": "rev-test",
                    "done_username": "release",
                    "is_deleted": False,
                },
            }
        ]
        fixture_path.write_text(json.dumps(fixture_data, indent=2) + "\n", encoding="utf-8")
        relative = fixture_path.relative_to(self.base_dir)
        self.env_refresh._fixture_files = lambda: [str(relative)]

        from django.core.management import call_command as django_call

        def _call_command(name, *args, **kwargs):
            self._commands.append((name, args, kwargs))
            if name == "loaddata":
                return django_call(name, *args, **kwargs)
            return None

        self.env_refresh.call_command = _call_command

        self.env_refresh.run_database_tasks()

        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)
        self.assertEqual(todo.done_on.replace(microsecond=0), done_on_value)
        self.assertEqual(todo.done_version, "9.9.9")
        self.assertEqual(todo.done_revision, "rev-test")
        self.assertEqual(todo.done_username, "release")
        self.assertTrue(todo.is_seed_data)


class EnvRefreshUserDataTests(TransactionTestCase):
    def setUp(self):
        base_dir = Path(settings.BASE_DIR)
        spec = importlib.util.spec_from_file_location(
            "env_refresh_user_data", base_dir / "env-refresh.py"
        )
        self.env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.env_refresh)
        self.data_dir = Path(settings.BASE_DIR) / "data"
        self.data_dir.mkdir(exist_ok=True)
        for item in list(self.data_dir.iterdir()):
            if item.is_file() and item.suffix == ".json":
                item.unlink()
            elif item.is_dir():
                for child in item.glob("*.json"):
                    child.unlink()
                try:
                    item.rmdir()
                except OSError:
                    pass

    def tearDown(self):
        for item in list(self.data_dir.iterdir()):
            if item.is_file() and item.suffix == ".json":
                item.unlink(missing_ok=True)
            elif item.is_dir():
                for child in item.glob("*.json"):
                    child.unlink(missing_ok=True)
                try:
                    item.rmdir()
                except OSError:
                    pass

    def test_env_refresh_loads_user_fixtures(self):
        self._run_clean_env_refresh()
        User = get_user_model()
        user = User.objects.get(username="arthexis")
        todo = Todo.objects.create(request="Personal TODO")
        Todo.all_objects.filter(pk=todo.pk).update(is_user_data=True)
        dump_user_fixture(todo, user)
        fixture_path = self.data_dir / user.username / f"core_todo_{todo.pk}.json"
        self.assertTrue(fixture_path.exists())

        Todo.objects.all().delete()
        self.assertFalse(Todo.objects.filter(request="Personal TODO").exists())

        self._run_clean_env_refresh()

        reloaded = Todo.all_objects.get(request="Personal TODO")
        self.assertTrue(reloaded.is_user_data)

    def _run_clean_env_refresh(self):
        connections.close_all()
        db_name = connection.settings_dict.get("NAME")
        if db_name:
            db_path = Path(db_name)
            db_path.unlink(missing_ok=True)
        self.env_refresh.run_database_tasks(clean=True)


class SeedDataViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from glob import glob

        cls.node_role_fixtures = sorted(glob("nodes/fixtures/node_roles__*.json"))
        if cls.node_role_fixtures:
            call_command("loaddata", *cls.node_role_fixtures)
        NodeRole.objects.filter(pk=1).update(is_seed_data=True)
        cls.user = get_user_model().objects.create_superuser("sdadmin", password="pw")

    def setUp(self):
        user_data._seed_fixture_index.cache_clear()
        self.user = type(self).user
        self.client.force_login(self.user)

    def test_seed_data_view_shows_fixture(self):
        response = self.client.get(reverse("admin:seed_data"))
        self.assertContains(response, "node_roles__noderole_terminal.json")

    def test_node_role_fixtures_present(self):
        names = set(NodeRole.objects.values_list("name", flat=True))
        self.assertEqual(
            names,
            {"Terminal", "Watchtower", "Control", "Satellite", "Interface"},
        )

    def test_seed_data_view_scans_fixture_directory_once(self):
        user_data._seed_fixture_index.cache_clear()

        original_glob = Path.glob
        call_count = 0

        def counting_glob(self, pattern):
            nonlocal call_count
            call_count += 1
            return original_glob(self, pattern)

        with patch("core.user_data.Path.glob", new=counting_glob):
            response = self.client.get(reverse("admin:seed_data"))
            self.assertEqual(response.status_code, 200)

        self.assertEqual(call_count, 1)
