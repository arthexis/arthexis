"""Database backend selection and configuration."""

import os
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .apps import ARTHEXIS_EXTERNAL_APPS
from .base import BASE_DIR
from .external_dbs import external_app_database_alias_mapping


def build_external_sqlite_databases(external_apps: list[str]) -> dict[str, dict[str, Path | str]]:
    """Return external-app SQLite database entries rooted in ``work/dbs``."""

    external_dbs_dir = BASE_DIR / "work" / "dbs"
    external_dbs_dir.mkdir(parents=True, exist_ok=True)

    configs: dict[str, dict[str, Path | str]] = {}
    for alias in external_app_database_alias_mapping(external_apps).values():
        configs[alias] = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": external_dbs_dir / f"{alias}.sqlite3",
            "OPTIONS": {"timeout": 60},
        }

    return configs

FORCED_DB_BACKEND = os.environ.get("ARTHEXIS_DB_BACKEND", "").strip().lower()
if FORCED_DB_BACKEND and FORCED_DB_BACKEND not in {"sqlite", "postgres"}:
    raise ImproperlyConfigured(
        "ARTHEXIS_DB_BACKEND must be 'sqlite' or 'postgres' when defined."
    )


if FORCED_DB_BACKEND == "postgres":
    _use_postgres = True
else:
    _use_postgres = False


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
    DATABASES.update(build_external_sqlite_databases(list(ARTHEXIS_EXTERNAL_APPS)))
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
    DATABASES.update(build_external_sqlite_databases(list(ARTHEXIS_EXTERNAL_APPS)))

DATABASE_ROUTERS = ["apps.core.dbrouters.ExternalAppDatabaseRouter"]
