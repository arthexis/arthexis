from __future__ import annotations

from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    """Rebuild all app migrations with a protective branch tag."""

    help = (
        "Remove all migrations under apps.*, regenerate them with makemigrations, "
        "and inject a branch tag to block applying the new tree on top of the "
        "destroyed schema."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apps-dir",
            dest="apps_dir",
            help="Override the apps directory (defaults to settings.APPS_DIR)",
        )
        parser.add_argument(
            "--branch-id",
            dest="branch_id",
            help="Stable identifier recorded by the branch tag operation.",
        )

    def handle(self, *args, **options):
        apps_dir = Path(
            options["apps_dir"]
            or getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps")
        )
        if not apps_dir.exists():
            self.stderr.write(f"Apps directory not found: {apps_dir}")
            return

        branch_id = options["branch_id"] or f"rebuild-{datetime.utcnow():%Y%m%d%H%M%S}"
        project_apps = self._collect_project_apps(apps_dir)

        call_command("clear_apps_migrations")
        call_command("makemigrations")

        tagged = self._tag_initial_migrations(apps_dir, branch_id, project_apps)
        if tagged:
            self.stdout.write("Tagged migrations with rebuild branch guards:")
            for path in tagged:
                self.stdout.write(f" - {path.relative_to(apps_dir)}")
        else:
            self.stdout.write(
                "No initial migrations were tagged; ensure makemigrations created them."
            )

    def _collect_project_apps(self, apps_dir: Path) -> list[str]:
        return sorted(
            {
                path.name
                for path in apps_dir.iterdir()
                if (path / "migrations").is_dir()
            }
        )

    def _tag_initial_migrations(
        self, apps_dir: Path, branch_id: str, project_apps: list[str]
    ) -> list[Path]:
        tagged: list[Path] = []
        for app_label in project_apps:
            migrations_dir = apps_dir / app_label / "migrations"
            if not migrations_dir.exists():
                continue

            initial_candidates = sorted(migrations_dir.glob("0001_*.py"))
            if not initial_candidates:
                continue

            target = initial_candidates[0]
            if self._inject_guard(target, branch_id, project_apps):
                tagged.append(target)
        return tagged

    def _inject_guard(
        self, migration_path: Path, branch_id: str, project_apps: list[str]
    ) -> bool:
        content = migration_path.read_text()
        if "BranchTagOperation" in content:
            return False

        import_hooks = (
            "from django.db import migrations, models",
            "from django.db import migrations",
        )
        guard_import = "from utils.migration_branches import BranchTagOperation"
        if guard_import not in content:
            for import_hook in import_hooks:
                if import_hook in content:
                    content = content.replace(
                        import_hook, f"{import_hook}\n{guard_import}", 1
                    )
                    break
            else:
                content = f"{guard_import}\n{content}"

        migration_label = (
            f"{migration_path.parent.parent.name}.{migration_path.stem}"
        )
        guard_line = (
            "    operations = [\n"
            f"        BranchTagOperation(\"{branch_id}\", "
            f"migration_label=\"{migration_label}\", "
            f"project_apps={tuple(project_apps)}),\n"
        )
        marker = "    operations = [\n"
        if marker not in content:
            raise ValueError(
                f"Could not find operations block in migration {migration_path}"
            )
        content = content.replace(marker, guard_line, 1)
        migration_path.write_text(content)
        return True
