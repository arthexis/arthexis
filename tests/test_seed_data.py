import os
import sys
import json
import shutil
import importlib.util
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.conf import settings
from nodes.models import Node, NodeRole
from core.models import OdooProfile, SecurityGroup
from django.contrib.sites.models import Site
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.management import call_command
import socket


class SeedDataEntityTests(TestCase):
    def test_preserve_seed_data_on_create(self):
        role = NodeRole.objects.create(name="Tester", is_seed_data=True)
        self.assertTrue(NodeRole.all_objects.get(pk=role.pk).is_seed_data)


class EntityInheritanceTests(TestCase):
    def test_local_models_inherit_entity(self):
        from django.apps import apps
        from core.entity import Entity

        allowed = {
            "core.UserDatum",
            "core.SecurityGroup",
            "pages.SiteProxy",
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
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        content = response.content.decode()
        self.assertIn('name="_seed_datum"', content)
        self.assertIn("Seed Datum", content)
        self.assertIn('name="_user_datum"', content)
        self.assertIn("User Datum", content)
        self.assertLess(
            content.index('name="_user_datum"'),
            content.index('name="_seed_datum"'),
        )

    def test_checkbox_has_form_attribute(self):
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
        response = self.client.get(url)
        form_id = f"{self.profile._meta.model_name}_form"
        self.assertContains(response, f'name="_seed_datum" form="{form_id}"')

    def test_checkbox_not_displayed_for_non_entity(self):
        group = SecurityGroup.objects.create(name="Temp")
        url = reverse(
            "admin:post_office_workgroupsecuritygroup_change", args=[group.pk]
        )
        response = self.client.get(url)
        self.assertNotContains(response, 'name="_seed_datum"')
        self.assertNotContains(response, 'name="_user_datum"')
        from django.contrib import admin
        from core.admin import WorkgroupSecurityGroup
        from core.user_data import EntityModelAdmin

        self.assertNotIsInstance(
            admin.site._registry[WorkgroupSecurityGroup], EntityModelAdmin
        )

    def test_entity_admins_auto_patched(self):
        from django.contrib import admin
        from core.entity import Entity
        from core.user_data import EntityModelAdmin

        for model, model_admin in admin.site._registry.items():
            if issubclass(model, Entity):
                self.assertIsInstance(model_admin, EntityModelAdmin)

    def test_seed_datum_persists_after_save(self):
        OdooProfile.all_objects.filter(pk=self.profile.pk).update(is_seed_data=True)
        url = reverse("admin:core_odooprofile_change", args=[self.profile.pk])
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

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                django_call(name, *args, **kwargs)
            # ignore other commands

        env_refresh.call_command = fake_call_command
        env_refresh.run_database_tasks()
        role = NodeRole.all_objects.get(pk=999)
        self.assertTrue(role.is_seed_data)
        shutil.rmtree(tmp_dir)


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


class SeedDataViewTests(TestCase):
    def setUp(self):
        call_command("loaddata", "nodes/fixtures/node_roles.json")
        NodeRole.objects.filter(pk=1).update(is_seed_data=True)
        User = get_user_model()
        self.user = User.objects.create_superuser("sdadmin", password="pw")
        self.client.login(username="sdadmin", password="pw")

    def test_seed_data_view_shows_fixture(self):
        response = self.client.get(reverse("admin:seed_data"))
        self.assertContains(response, "node_roles.json")
