"""Regression coverage for ``register_site_apps`` application seeding."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test.utils import override_settings

from apps.app.models import Application


@pytest.mark.parametrize(
    ("settings_kwargs", "expected_app_label"),
    [
        ({"PROJECT_LOCAL_APPS": ("apps.features",), "LOCAL_APPS": []}, "features"),
        ({"PROJECT_LOCAL_APPS": None, "LOCAL_APPS": ["apps.features"]}, "features"),
    ],
)
def test_register_site_apps_seeds_from_configured_local_apps(
    db, settings_kwargs: dict[str, object], expected_app_label: str
) -> None:
    """Command should seed app rows from configured local app settings on fresh installs."""

    Application.objects.all().delete()

    with override_settings(**settings_kwargs):
        call_command("register_site_apps")

    assert Application.objects.filter(name=expected_app_label).exists()
