"""Regression tests for grouped settings in the Django config admin view."""

from __future__ import annotations

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from apps.core import environment
from apps.core.environment import _config_view, _environment_view, _group_django_settings


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


def test_config_view_renders_grouped_sections(db, monkeypatch) -> None:
    """The config admin view should expose grouped sections and section links."""
    monkeypatch.setattr(
        environment,
        "_get_django_settings",
        lambda: [
            ("AWS_ACCESS_KEY_ID", "key"),
            ("AWS_SECRET_ACCESS_KEY", "secret"),
            ("DEBUG", True),
        ],
    )
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
    assert 'href="#config-section-1">AWS<' in response.rendered_content
    assert 'href="#config-section-2">Other<' in response.rendered_content


def test_environment_view_exposes_user_values(db, tmp_path, settings) -> None:
    """The environment admin view should include persisted user-specific values."""
    settings.BASE_DIR = tmp_path
    user = get_user_model().objects.create_superuser(
        username="admin2",
        email="admin2@example.com",
        password="admin123",
    )
    user_env_dir = tmp_path / "var" / "user_env"
    user_env_dir.mkdir(parents=True)
    (user_env_dir / f"{user.pk}.env").write_text("PATH=/custom/bin\n")

    request = RequestFactory().get("/admin/environment/")
    request.user = user

    response = _environment_view(request)
    response.render()

    assert response.status_code == 200
    path_row = next(
        row
        for row in response.context_data["env_rows"]
        if row["key"] == "PATH"
    )
    assert path_row["user_value"] == "/custom/bin"


def test_environment_view_post_persists_user_values(db, tmp_path, settings) -> None:
    """Posting environment user values should persist to the personal ``.env`` file."""
    settings.BASE_DIR = tmp_path
    user = get_user_model().objects.create_superuser(
        username="admin3",
        email="admin3@example.com",
        password="admin123",
    )

    request = RequestFactory().post(
        "/admin/environment/",
        data={"user_value_PATH": "/post/value"},
    )
    request.user = user
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))

    response = _environment_view(request)
    response.render()

    assert response.status_code == 200
    env_file = tmp_path / "var" / "user_env" / f"{user.pk}.env"
    assert env_file.exists()
    assert "PATH=/post/value" in env_file.read_text()


def test_environment_view_trims_user_env_keys(db, tmp_path, settings) -> None:
    """User env keys with spaces around '=' should still match known variables."""
    settings.BASE_DIR = tmp_path
    user = get_user_model().objects.create_superuser(
        username="admin4",
        email="admin4@example.com",
        password="admin123",
    )
    user_env_dir = tmp_path / "var" / "user_env"
    user_env_dir.mkdir(parents=True)
    (user_env_dir / f"{user.pk}.env").write_text("PATH = /spaced/value\n")

    request = RequestFactory().get("/admin/environment/")
    request.user = user

    response = _environment_view(request)
    response.render()

    path_row = next(
        row
        for row in response.context_data["env_rows"]
        if row["key"] == "PATH"
    )
    assert path_row["user_value"] == " /spaced/value"
