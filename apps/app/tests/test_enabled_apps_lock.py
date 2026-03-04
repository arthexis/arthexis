"""Tests for enabled application lock synchronization."""

from __future__ import annotations

import pytest

from apps.app.models import Application
from utils.enabled_apps_lock import get_enabled_apps_lock_path


@pytest.mark.django_db
@pytest.mark.regression
def test_application_save_updates_enabled_apps_lock(settings):
    """Regression: saving application enablement rewrites the enabled-app lock file."""

    Application.objects.filter(name__in=["enabled-core", "enabled-site"]).delete()
    app = Application.objects.create(name="enabled-core", enabled=True)
    lock_path = get_enabled_apps_lock_path(settings.BASE_DIR)

    assert lock_path.exists()
    assert app.name in lock_path.read_text(encoding="utf-8").splitlines()

    app.enabled = False
    app.save(update_fields=["enabled"])

    assert app.name not in lock_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.django_db
@pytest.mark.regression
def test_application_delete_updates_enabled_apps_lock(settings):
    """Regression: deleting an enabled app rewrites the lock file without that entry."""

    Application.objects.filter(name__in=["enabled-core", "enabled-site"]).delete()
    first = Application.objects.create(name="enabled-core", enabled=True)
    second = Application.objects.create(name="enabled-site", enabled=True)
    lock_path = get_enabled_apps_lock_path(settings.BASE_DIR)

    first.delete()

    assert lock_path.exists()
    lock_entries = lock_path.read_text(encoding="utf-8").splitlines()
    assert first.name not in lock_entries
    assert second.name in lock_entries
