"""Management command to inventory shell scripts across the repository."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.app.models import Application
from apps.shells.models import AppShellScript, BaseShellScript


class Command(BaseCommand):
    """Synchronize base and app shell scripts into dedicated inventory models."""

    help = "Inventory shell scripts from repository root and scripts/ directory."

    def add_arguments(self, parser):
        """Register optional arguments used by this command."""

        parser.add_argument(
            "--base-path",
            default=".",
            help="Repository base path used to discover scripts.",
        )
        parser.add_argument(
            "--manager-app",
            default="ops",
            help="Application name used as manager for app shell scripts.",
        )

    def handle(self, *args, **options):
        """Perform inventory synchronization and report counts."""

        del args
        base_path = Path(options["base_path"]).resolve()
        scripts_root = base_path / "scripts"
        if not scripts_root.exists():
            raise CommandError(f"scripts directory not found under {base_path}")

        manager_app_name = str(options["manager_app"]).strip()
        if not manager_app_name:
            raise CommandError("manager app name cannot be blank")

        manager_app, _ = Application.all_objects.get_or_create(name=manager_app_name)

        base_scripts = sorted(path for path in base_path.glob("*.sh") if path.is_file())
        app_scripts = sorted(path for path in scripts_root.rglob("*.sh") if path.is_file())

        base_seen_paths: set[str] = set()
        app_seen_paths: set[str] = set()
        created = 0
        updated = 0

        for script_path in base_scripts:
            rel_path = script_path.relative_to(base_path).as_posix()
            _, was_created = BaseShellScript.all_objects.update_or_create(
                path=rel_path,
                defaults={"name": script_path.name},
            )
            created += int(was_created)
            updated += int(not was_created)
            base_seen_paths.add(rel_path)

        for script_path in app_scripts:
            rel_path = script_path.relative_to(base_path).as_posix()
            _, was_created = AppShellScript.all_objects.update_or_create(
                path=rel_path,
                defaults={"name": script_path.name, "managed_by": manager_app},
            )
            created += int(was_created)
            updated += int(not was_created)
            app_seen_paths.add(rel_path)

        removed_base, _ = BaseShellScript.all_objects.exclude(path__in=base_seen_paths).delete()
        removed_app, _ = AppShellScript.all_objects.exclude(path__in=app_seen_paths).delete()
        removed = removed_base + removed_app
        self.stdout.write(
            self.style.SUCCESS(
                f"Inventory complete: created={created}, updated={updated}, removed={removed}"
            )
        )
