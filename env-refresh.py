#!/usr/bin/env python
"""Development maintenance tasks.

Ensures migrations are up to date and fixes inconsistent histories.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import json
import tempfile

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
    except OperationalError as exc:
        if using_sqlite:
            connections.close_all()
            Path(default_db["NAME"]).unlink(missing_ok=True)
            call_command("migrate", interactive=False)
        else:  # pragma: no cover - unreachable in sqlite
            try:
                import psycopg
                from psycopg import sql

                params = {
                    "dbname": "postgres",
                    "user": default_db.get("USER", ""),
                    "password": default_db.get("PASSWORD", ""),
                    "host": default_db.get("HOST", ""),
                    "port": default_db.get("PORT", ""),
                }
                with psycopg.connect(**params, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            sql.SQL("CREATE DATABASE {}" ).format(
                                sql.Identifier(default_db["NAME"])
                            )
                        )
                call_command("migrate", interactive=False)
            except Exception:
                raise exc

    fixtures = _fixture_files()
    if fixtures:
        with tempfile.TemporaryDirectory() as tmpdir:
            patched: list[str] = []
            for name in fixtures:
                source = Path(settings.BASE_DIR, name)
                with source.open() as f:
                    data = json.load(f)
                for obj in data:
                    model_label = obj.get("model", "")
                    try:
                        model = apps.get_model(model_label)
                    except LookupError:
                        continue
                    if any(f.name == "is_seed_data" for f in model._meta.fields):
                        obj.setdefault("fields", {})["is_seed_data"] = True
                dest = Path(tmpdir, Path(name).name)
                with dest.open("w") as f:
                    json.dump(data, f)
                patched.append(str(dest))
            call_command("loaddata", *patched)

    # Ensure Application and SiteApplication entries exist for local apps
    call_command("register_site_apps")


TASKS = {"database": run_database_tasks}


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

