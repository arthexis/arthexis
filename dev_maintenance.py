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
        Path(default_db["NAME"]).unlink(missing_ok=True)
        call_command("migrate", interactive=False)
    else:
        raise

proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
if proc.stdout.strip():
    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", "Auto migrations"], check=False)
    subprocess.run(["git", "push"], check=False)
