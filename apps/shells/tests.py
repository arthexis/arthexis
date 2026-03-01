"""Tests for shell script inventory models and command."""

from __future__ import annotations

from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase

from apps.app.models import Application
from apps.shells.models import AppShellScript, BaseShellScript


class AppShellScriptModelTests(TestCase):
    """Verify AppShellScript model constraints."""

    def test_app_script_requires_manager_application(self):
        """Ensure app shell scripts require a linked manager application."""

        with self.assertRaises(IntegrityError):
            AppShellScript.objects.create(
                name="example.sh",
                path="scripts/example.sh",
            )


class InventoryShellScriptsCommandTests(TestCase):
    """Validate inventory synchronization for existing shell scripts."""

    def test_command_inventories_base_and_app_scripts(self):
        """Inventory command should create both base and app shell records."""

        repo_root = Path(__file__).resolve().parents[2]
        call_command("inventory_shell_scripts", base_path=str(repo_root), manager_app="ops")

        self.assertTrue(BaseShellScript.objects.filter(path="start.sh").exists())
        self.assertTrue(
            AppShellScript.objects.filter(
                path="scripts/helpers/common.sh",
                managed_by__name="ops",
            ).exists()
        )

    def test_command_rejects_blank_manager_app(self):
        """Inventory command should reject blank manager app values."""

        repo_root = Path(__file__).resolve().parents[2]
        with self.assertRaisesMessage(CommandError, "manager app name cannot be blank"):
            call_command("inventory_shell_scripts", base_path=str(repo_root), manager_app="")


class ShellScriptsFixtureCoverageTests(TestCase):
    """Ensure fixture-backed records include representative repository scripts."""

    fixtures = [
        "shells__base_shell_scripts_inventory.json",
        "shells__app_shell_scripts_inventory.json",
    ]

    def test_fixture_contains_existing_scripts(self):
        """Fixture should include known base and app scripts present in the repo."""

        self.assertTrue(BaseShellScript.objects.filter(path="stop.sh").exists())
        self.assertTrue(AppShellScript.objects.filter(path="scripts/service-start.sh").exists())

    def test_fixture_uses_ops_manager_for_app_scripts(self):
        """Fixture app script records should be associated with the ops app."""

        ops = Application.objects.get(name="ops")
        self.assertTrue(AppShellScript.objects.filter(managed_by=ops).exists())
