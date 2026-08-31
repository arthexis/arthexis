"""Regression tests for the unified create management command."""

from __future__ import annotations

import io
from pathlib import Path

from django.conf import settings
from django.core.management import call_command


def _seed_apps_root(base_dir: Path) -> Path:
    apps_dir = base_dir / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "__init__.py").write_text(
        '"""Project application packages."""\n', encoding="utf-8"
    )
    settings.BASE_DIR = base_dir
    settings.APPS_DIR = apps_dir
    return apps_dir


def test_create_app_generates_expected_modules(tmp_path):
    """create app should scaffold app, routes, views, urls, and admin wiring."""

    apps_dir = _seed_apps_root(tmp_path)

    stdout = io.StringIO()
    call_command("create", "app", "catalog", stdout=stdout)

    expected_paths = [
        apps_dir / "catalog" / "apps.py",
        apps_dir / "catalog" / "models.py",
        apps_dir / "catalog" / "admin.py",
        apps_dir / "catalog" / "views.py",
        apps_dir / "catalog" / "urls.py",
        apps_dir / "catalog" / "routes.py",
        apps_dir / "catalog" / "manifest.py",
        apps_dir / "catalog" / "migrations" / "__init__.py",
    ]
    for path in expected_paths:
        assert path.exists(), f"Expected scaffold file was not created: {path}"

    routes_text = (apps_dir / "catalog" / "routes.py").read_text(encoding="utf-8")
    assert 'include("apps.catalog.urls")' in routes_text

    manifest_text = (apps_dir / "catalog" / "manifest.py").read_text(encoding="utf-8")
    assert 'DJANGO_APPS = [\n    "apps.catalog",\n]\n' in manifest_text

    output = stdout.getvalue()
    assert "makemigrations catalog" in output
