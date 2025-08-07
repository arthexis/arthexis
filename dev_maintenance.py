#!/usr/bin/env python
"""Development maintenance tasks.

Ensures migrations are up to date and fixes inconsistent histories.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import django
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.db.utils import OperationalError
from django.db import connections
from django.apps import apps

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

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
    else:
        raise

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


if _has_non_initial_migrations():
    if using_sqlite:
        connections.close_all()
        Path(default_db["NAME"]).unlink(missing_ok=True)
    else:
        call_command("migrate", "zero", interactive=False)
    call_command("migrate", interactive=False)
    # Squash migrations back to a single initial state after a successful migrate
    call_command("reset_migrations")
    call_command("migrate", interactive=False, fake_initial=True)

proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
if proc.stdout.strip():
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "Auto migrations"], check=False)
    subprocess.run(["git", "push"], check=False)
