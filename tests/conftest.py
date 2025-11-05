from __future__ import annotations

import os
import shutil
import subprocess
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


def safe_setup(*args, **kwargs):
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
        _original_setup(*args, **kwargs)

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
    config.addinivalue_line(
        "markers", "feature(slug): mark test as requiring a node feature"
    )
    config.addinivalue_line(
        "markers", "django_db: mark test as requiring database access"
    )


_SANITIZED_COPY_IGNORES = (
    ".git",
    "__pycache__",
    "*.py[cod]",
    "*.pyd",
    "*.so",
    "*.dylib",
    "*.log",
    "logs",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage.*",
    "htmlcov",
    "build",
    "dist",
    "env",
    "venv",
    ".venv",
)


def _create_sanitized_snapshot(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*_SANITIZED_COPY_IGNORES),
    )


def _initialize_git_repository(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Test snapshot"], cwd=path, check=True)


@pytest.fixture(scope="session")
def sanitized_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return a sanitized copy of the repository for test workspaces."""

    base_dir = tmp_path_factory.mktemp("sanitized-repo")
    workspace = base_dir / "repo"
    _create_sanitized_snapshot(ROOT, workspace)
    _initialize_git_repository(workspace)
    return workspace


@pytest.fixture
def prepared_repo(tmp_path: Path, sanitized_repo: Path) -> Path:
    """Provide a mutable copy of the sanitized repository for individual tests."""

    destination = tmp_path / "repo"
    shutil.copytree(sanitized_repo, destination)
    return destination


def _env_flag(name: str) -> bool:
    """Return ``True`` when the environment flag ``name`` is enabled."""

    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _feature_filter() -> set[str] | None:
    value = os.environ.get("NODE_FEATURES")
    if value is None:
        return None
    features = {item.strip() for item in value.split(",") if item.strip()}
    return features


def _feature_skip_reason(required: set[str], role: str | None) -> str:
    formatted = ", ".join(sorted(required))
    if role:
        return f"{formatted} feature(s) not enabled for {role} role"
    return f"{formatted} feature(s) not enabled for this test run"


def pytest_collection_modifyitems(config, items):
    role = os.environ.get("NODE_ROLE")
    require_markers = _env_flag("NODE_ROLE_ONLY")
    role_skip = pytest.mark.skip(reason=f"not run for {role} role") if role else None
    missing_skip = (
        pytest.mark.skip(reason="missing role marker while NODE_ROLE_ONLY is enabled")
        if require_markers
        else None
    )
    features = _feature_filter()

    for item in items:
        roles = {m.args[0] for m in item.iter_markers("role") if m.args}
        if role and roles and role not in roles:
            item.add_marker(role_skip)
        if require_markers and not roles:
            item.add_marker(missing_skip)
        required = {m.args[0] for m in item.iter_markers("feature") if m.args}
        if features is not None and required and required.isdisjoint(features):
            item.add_marker(
                pytest.mark.skip(reason=_feature_skip_reason(required, role))
            )
