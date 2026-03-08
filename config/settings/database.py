"""Database backend selection and configuration."""

import contextlib
import os
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from config.settings_helpers import should_probe_postgres

from .base import BASE_DIR

FORCED_DB_BACKEND = os.environ.get("ARTHEXIS_DB_BACKEND", "").strip().lower()
if FORCED_DB_BACKEND and FORCED_DB_BACKEND not in {"sqlite", "postgres"}:
    raise ImproperlyConfigured(
        "ARTHEXIS_DB_BACKEND must be 'sqlite' or 'postgres' when defined."
    )


def _postgres_available() -> bool:
    """Return whether the configured PostgreSQL endpoint is reachable quickly.

    Startup probes should fail fast so local tooling (for example the VS Code
    migration watcher) can fall back to SQLite when PostgreSQL is unavailable.
    """

    if FORCED_DB_BACKEND == "sqlite":
        return False
    if not should_probe_postgres():
        return False
    try:
        import psycopg
    except ImportError:
        return False

    try:
        connect_timeout = int(os.environ.get("ARTHEXIS_POSTGRES_PROBE_TIMEOUT", "1"))
    except (TypeError, ValueError):
        connect_timeout = 1

    if connect_timeout <= 0:
        connect_timeout = 1

    params = {
        "dbname": os.environ.get("POSTGRES_DB", "postgres"),
        "user": os.environ.get("POSTGRES_USER", "postgres"),
        "password": os.environ.get("POSTGRES_PASSWORD", ""),
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "connect_timeout": connect_timeout,
    }
    try:
        with contextlib.closing(psycopg.connect(**params)):
            return True
    except (psycopg.Error, OSError):
        return False


if FORCED_DB_BACKEND == "postgres":
    _use_postgres = True
elif FORCED_DB_BACKEND == "sqlite":
    _use_postgres = False
else:
    _use_postgres = _postgres_available()


if _use_postgres:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "postgres"),
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "OPTIONS": {"options": "-c timezone=UTC"},
            "TEST": {
                "NAME": f"{os.environ.get('POSTGRES_DB', 'postgres')}_test",
            },
        }
    }
else:
    _sqlite_override = os.environ.get("ARTHEXIS_SQLITE_PATH")
    if _sqlite_override:
        SQLITE_DB_PATH = Path(_sqlite_override)
    else:
        SQLITE_DB_PATH = BASE_DIR / "db.sqlite3"

    def _sqlite_parent_is_writable(path: Path) -> bool:
        """Return whether ``path.parent`` supports SQLite sidecar writes."""

        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=parent):
                pass
        except OSError:
            return False
        return True

    _sqlite_test_override = os.environ.get("ARTHEXIS_SQLITE_TEST_PATH")
    if _sqlite_test_override:
        SQLITE_TEST_DB_PATH = Path(_sqlite_test_override)
    else:
        _shm_candidate = Path("/dev/shm") / "arthexis" / "test_db.sqlite3"
        _tmp_candidate = Path(tempfile.gettempdir()) / "arthexis" / "test_db.sqlite3"
        SQLITE_TEST_DB_PATH = (
            _shm_candidate
            if _sqlite_parent_is_writable(_shm_candidate)
            else _tmp_candidate
        )

    SQLITE_TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": SQLITE_DB_PATH,
            "OPTIONS": {"timeout": 60},
            "TEST": {"NAME": SQLITE_TEST_DB_PATH},
        }
    }
