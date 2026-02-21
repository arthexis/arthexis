"""Regression tests for grouped settings in the Django config admin view."""

from __future__ import annotations

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.core.environment import _config_view, _group_django_settings


def test_group_django_settings_uses_repeated_prefix_sections() -> None:
    """Settings with repeated prefixes should be grouped into named sections."""
    grouped = _group_django_settings(
        [
            ("AWS_ACCESS_KEY_ID", "key"),
            ("AWS_SECRET_ACCESS_KEY", "secret"),
            ("DEBUG", True),
        ]
    )

    assert grouped[0]["name"] == "AWS"
    assert grouped[0]["settings"] == [
        ("AWS_ACCESS_KEY_ID", "key"),
        ("AWS_SECRET_ACCESS_KEY", "secret"),
    ]
    assert grouped[1]["name"] == "Other"
    assert grouped[1]["settings"] == [("DEBUG", True)]


def test_group_django_settings_keeps_single_prefixes_in_other() -> None:
    """Unique prefixes should be combined in a single Other section."""
    grouped = _group_django_settings(
        [
            ("DEBUG", True),
            ("SECRET_KEY", "x"),
        ]
    )

    assert grouped == [
        {"name": "Other", "settings": [("DEBUG", True), ("SECRET_KEY", "x")]}
    ]


def test_config_view_renders_grouped_sections(db) -> None:
    """The config admin view should expose grouped sections to the template context."""
    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    request = RequestFactory().get("/admin/config/")
    request.user = user

    response = _config_view(request)
    response.render()

    assert response.status_code == 200
    assert "config_sections" in response.context_data
    assert response.context_data["site_title"] == site.site_title
