from __future__ import annotations

from pathlib import Path


NGINX_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_RUNTIME_SHELL_ENTRIES = {
    "__init__.py",
    "apps.py",
    "migrations",
    "tests",
}


def test_nginx_package_is_migration_only_shell():
    """Retired nginx code must not regain operational Django modules."""

    package_entries = {
        path.name
        for path in NGINX_PACKAGE_ROOT.iterdir()
        if path.name != "__pycache__"
    }

    assert package_entries == ALLOWED_RUNTIME_SHELL_ENTRIES
