import os
import sys
from pathlib import Path

import pytest

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
    :func:`django.setup` leaves the database without any tables. Many tests
    exercise ORM behaviour and expect the schema to exist. Configure Django to
    use the dedicated test database and run ``migrate`` the first time it is
    configured so the database file and tables are created automatically.
    """

    if not apps.ready:
        from django.conf import settings

        # Switch to the test database defined in settings to avoid touching
        # development data.
        test_name = settings.DATABASES["default"].get("TEST", {}).get("NAME")
        if test_name:
            db_name = settings.DATABASES["default"]["NAME"]
            settings.DATABASES["default"]["NAME"] = str(test_name)
            engine = settings.DATABASES["default"].get("ENGINE", "")
            if engine.endswith("sqlite3"):
                db_path = Path(test_name)
                if db_path.exists():
                    db_path.unlink()
            else:
                import psycopg

                params = {
                    "dbname": db_name,
                    "user": settings.DATABASES["default"].get("USER", ""),
                    "password": settings.DATABASES["default"].get("PASSWORD", ""),
                    "host": settings.DATABASES["default"].get("HOST", ""),
                    "port": settings.DATABASES["default"].get("PORT", ""),
                }
                with psycopg.connect(**params) as conn:
                    conn.autocommit = True
                    with conn.cursor() as cursor:
                        cursor.execute(
                            f'DROP DATABASE IF EXISTS "{test_name}" WITH (FORCE)'
                        )
                        cursor.execute(f'CREATE DATABASE "{test_name}"')

        # Perform the regular Django setup after configuring the test database
        _original_setup()

        from django.db import connections

        if test_name:
            connections.databases["default"]["NAME"] = str(test_name)

        # Speed up tests by using a lightweight password hasher and disabling
        # password validation checks. The default PBKDF2 hasher uses one
        # million iterations which is unnecessarily slow for unit tests and
        # would make the migration phase (which creates a default superuser)
        # take several seconds.
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
        settings.AUTH_PASSWORD_VALIDATORS = []

        # Apply migrations to create the database schema
        call_command("migrate", run_syncdb=True, verbosity=0)


django.setup = safe_setup
safe_setup()


@pytest.fixture(autouse=True)
def ensure_test_database():
    """Fail fast if tests attempt to use the development database."""
    from django.conf import settings

    name = str(settings.DATABASES["default"]["NAME"])
    if "test" not in name:
        raise RuntimeError("Tests must run against the test database")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "role(name): mark test as associated with a node role"
    )


def pytest_collection_modifyitems(config, items):
    role = os.environ.get("NODE_ROLE")
    if not role:
        return
    skip = pytest.mark.skip(reason=f"not run for {role} role")
    for item in items:
        roles = {m.args[0] for m in item.iter_markers("role")}
        if roles and role not in roles:
            item.add_marker(skip)
