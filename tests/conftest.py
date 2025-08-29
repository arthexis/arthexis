import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import django
from django.apps import apps
from django.core.management import call_command

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Keep a reference to the original setup function so we can call it lazily
_original_setup = django.setup


def safe_setup():
    """Initialize Django and ensure the test database is migrated.

    Pytest does not use ``pytest-django`` for these tests, so simply calling
    :func:`django.setup` leaves the SQLite database without any tables.  Many
    tests exercise ORM behaviour and expect the schema to exist.  Run
    ``migrate`` the first time Django is configured so the database file and
    tables are created automatically.
    """

    if not apps.ready:
        # Start from a clean SQLite database for every test run.
        db_path = ROOT / "db.sqlite3"
        if db_path.exists():
            db_path.unlink()

        # Perform the regular Django setup after cleaning up the database
        _original_setup()
        from django.conf import settings

        # Speed up tests by using a lightweight password hasher and disabling
        # password validation checks. The default PBKDF2 hasher uses one
        # million iterations which is unnecessarily slow for unit tests and
        # would make the migration phase (which creates a default superuser)
        # take several seconds.
        settings.PASSWORD_HASHERS = [
            "django.contrib.auth.hashers.MD5PasswordHasher"
        ]
        settings.AUTH_PASSWORD_VALIDATORS = []

        # Apply migrations to create the database schema
        call_command("migrate", run_syncdb=True, verbosity=0)


django.setup = safe_setup
safe_setup()
