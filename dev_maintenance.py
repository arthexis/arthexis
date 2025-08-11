#!/usr/bin/env python
"""Development maintenance tasks.

Ensures migrations are up to date and fixes inconsistent histories.
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.db.utils import OperationalError


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def _local_app_labels() -> list[str]:
    base_dir = Path(settings.BASE_DIR)
    labels: list[str] = []
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path)
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            continue
        labels.append(app_config.label)
    return labels


def _fixture_files() -> list[str]:
    """Return all JSON fixtures in the project."""
    base_dir = Path(settings.BASE_DIR)
    fixtures = [
        str(path.relative_to(base_dir))
        for path in base_dir.glob("**/fixtures/*.json")
    ]
    return sorted(fixtures)


def run_database_tasks() -> None:
    """Run all database related maintenance steps."""
    default_db = settings.DATABASES["default"]
    using_sqlite = default_db["ENGINE"] == "django.db.backends.sqlite3"

    local_apps = _local_app_labels()

    try:
        call_command("makemigrations", *local_apps, interactive=False)
    except CommandError:
        call_command("makemigrations", *local_apps, merge=True, interactive=False)
    except InconsistentMigrationHistory:
        if using_sqlite:
            connections.close_all()
            Path(default_db["NAME"]).unlink(missing_ok=True)
            call_command("makemigrations", *local_apps, interactive=False)
        else:  # pragma: no cover - unreachable in sqlite
            raise

    try:
        call_command("migrate", interactive=False)
    except InconsistentMigrationHistory:
        call_command("reset_ocpp_migrations")
        call_command("migrate", interactive=False)
    except OperationalError:
        if using_sqlite:
            connections.close_all()
            Path(default_db["NAME"]).unlink(missing_ok=True)
            call_command("migrate", interactive=False)
        else:  # pragma: no cover - unreachable in sqlite
            raise

    for fixture in _fixture_files():
        call_command("loaddata", fixture)

    # Ensure Application and SiteApplication entries exist for local apps
    call_command("register_site_apps")


def run_git_tasks() -> None:
    """Commit and push auto-generated migrations."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True
    )
    if proc.stdout.strip():
        subprocess.run(["git", "add", "-A"], check=False)
        subprocess.run(["git", "commit", "-m", "Auto migrations"], check=False)
        subprocess.run(["git", "push"], check=False)


TASKS = {"database": run_database_tasks, "git": run_git_tasks}


def main(selected: list[str] | None = None) -> None:
    """Run the selected maintenance tasks."""
    to_run = selected or list(TASKS)
    for name in to_run:
        TASKS[name]()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Development maintenance tasks")
    parser.add_argument(
        "tasks", nargs="*", choices=TASKS.keys(), help="Tasks to run"
    )
    args = parser.parse_args()
    main(args.tasks)

