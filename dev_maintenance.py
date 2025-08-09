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


def _has_non_initial_migrations() -> bool:
    base_dir = Path(settings.BASE_DIR)
    for app_config in apps.get_app_configs():
        app_path = Path(app_config.path)
        try:
            app_path.relative_to(base_dir)
        except ValueError:
            continue
        migrations_path = app_path / "migrations"
        if not migrations_path.exists():
            continue
        for item in migrations_path.iterdir():
            if item.name in {"__init__.py", "0001_initial.py"}:
                continue
            if item.is_file() and item.suffix == ".py":
                return True
    return False


def run_database_tasks() -> None:
    """Run all database related maintenance steps."""
    default_db = settings.DATABASES["default"]
    using_sqlite = default_db["ENGINE"] == "django.db.backends.sqlite3"

    try:
        call_command("makemigrations", interactive=False)
    except CommandError:
        call_command("makemigrations", merge=True, interactive=False)

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

    if _has_non_initial_migrations():
        if using_sqlite:
            connections.close_all()
            Path(default_db["NAME"]).unlink(missing_ok=True)
        else:
            call_command("migrate", "zero", interactive=False)
        call_command("migrate", interactive=False)
        # Squash migrations back to a single initial state
        call_command("reset_migrations")
        call_command("migrate", interactive=False, fake_initial=True)

    call_command("loaddata", "ocpp_simulators")


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

