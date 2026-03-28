"""Regression coverage for ``register_site_apps`` application seeding."""

from __future__ import annotations

from django.conf import settings
from django.core.management import call_command

from apps.app.models import Application


def test_register_site_apps_uses_project_local_apps_when_local_apps_missing(db) -> None:
    """Command should seed app rows from ``PROJECT_LOCAL_APPS`` on fresh installs."""

    assert "apps.features" in settings.PROJECT_LOCAL_APPS
    Application.objects.all().delete()

    call_command("register_site_apps")

    assert Application.objects.filter(name="features").exists()
