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


def test_create_model_updates_existing_app_files(tmp_path):
    """create model should wire models/admin/views/urls/routes inside existing app."""

    apps_dir = _seed_apps_root(tmp_path)
    call_command("create", "app", "support")

    call_command("create", "model", "support", "ticket")

    models_text = (apps_dir / "support" / "models.py").read_text(encoding="utf-8")
    admin_text = (apps_dir / "support" / "admin.py").read_text(encoding="utf-8")
    views_text = (apps_dir / "support" / "views.py").read_text(encoding="utf-8")
    urls_text = (apps_dir / "support" / "urls.py").read_text(encoding="utf-8")
    routes_text = (apps_dir / "support" / "routes.py").read_text(encoding="utf-8")

    assert "class Ticket(models.Model):" in models_text
    assert "@admin.register(Ticket)" in admin_text
    assert "class TicketListView(ListView):" in views_text
    assert 'name="ticket-list"' in urls_text
    assert 'include("apps.support.urls")' in routes_text


def test_create_app_backend_only_omits_web_modules(tmp_path):
    """create app --backend-only should omit web wiring files and add manifest marker."""

    apps_dir = _seed_apps_root(tmp_path)

    call_command("create", "app", "jobs", "--backend-only")

    assert (apps_dir / "jobs" / "apps.py").exists()
    assert (apps_dir / "jobs" / "models.py").exists()
    assert (apps_dir / "jobs" / "admin.py").exists()
    assert not (apps_dir / "jobs" / "views.py").exists()
    assert not (apps_dir / "jobs" / "urls.py").exists()
    assert not (apps_dir / "jobs" / "routes.py").exists()

    manifest_text = (apps_dir / "jobs" / "manifest.py").read_text(encoding="utf-8")
    assert "APP_STRUCTURE: backend-only" in manifest_text


def test_create_model_skips_web_wiring_for_backend_only_app(tmp_path):
    """create model should not generate views/urls/routes for backend-only apps."""

    apps_dir = _seed_apps_root(tmp_path)

    call_command("create", "app", "jobs", "--backend-only")
    call_command("create", "model", "jobs", "work_item")

    assert not (apps_dir / "jobs" / "views.py").exists()
    assert not (apps_dir / "jobs" / "urls.py").exists()
    assert not (apps_dir / "jobs" / "routes.py").exists()

    models_text = (apps_dir / "jobs" / "models.py").read_text(encoding="utf-8")
    admin_text = (apps_dir / "jobs" / "admin.py").read_text(encoding="utf-8")
    assert "class WorkItem(models.Model):" in models_text
    assert "@admin.register(WorkItem)" in admin_text
