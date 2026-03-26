"""Regression tests for Raspberry Pi imager admin UI actions."""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import RequestFactory
from django.urls import reverse

from apps.imager.services import ImagerBuildError
from apps.sites.templatetags.admin_extras import model_admin_actions


@pytest.mark.django_db
def test_imager_admin_changelist_renders_create_rpi_image_tool(client) -> None:
    """Regression: changelist should expose the create image object-tool button."""

    user = get_user_model().objects.create_superuser(
        username="imager-admin",
        email="imager-admin@example.com",
        password="secret",
    )
    client.force_login(user)

    response = client.get(reverse("admin:imager_raspberrypiimageartifact_changelist"))

    assert response.status_code == 200
    assert "Create RPI image" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_imager_dashboard_model_row_actions_include_create_rpi_image() -> None:
    """Regression: dashboard row actions should expose create image shortcut."""

    user = get_user_model().objects.create_superuser(
        username="imager-dashboard-admin",
        email="imager-dashboard-admin@example.com",
        password="secret",
    )
    request = RequestFactory().get("/admin/")
    request.user = user
    actions = model_admin_actions({"request": request}, "imager", "RaspberryPiImageArtifact")

    assert any(action["label"] == "Create RPI image" for action in actions)
    assert any(
        action["url"] == reverse("admin:imager_raspberrypiimageartifact_create_rpi_image")
        for action in actions
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload_overrides", "build_side_effect", "expected_status", "expected_redirect", "build_called", "customize_value", "expected_message"),
    [
        ({}, None, 302, True, True, True, None),
        ({"name": ""}, None, 200, False, False, None, "This field is required."),
        ({}, ImagerBuildError("build failed"), 200, False, True, True, "build failed"),
    ],
)
@patch("apps.imager.admin.build_rpi4b_image")
def test_imager_admin_create_rpi_image_view_submission_paths(
    mock_build,
    admin_client,
    payload_overrides,
    build_side_effect,
    expected_status,
    expected_redirect,
    build_called,
    customize_value,
    expected_message,
) -> None:
    """Regression: create image admin form validates, reports failures, and invokes build service."""

    if build_side_effect is not None:
        mock_build.side_effect = build_side_effect

    payload = {
        "name": "nightly",
        "base_image_uri": "/tmp/base.img",
        "output_dir": "build/rpi-imager",
        "download_base_uri": "https://downloads.example.com/imager",
        "git_url": "https://github.com/arthexis/arthexis.git",
    }
    payload.update(payload_overrides)

    response = admin_client.post(
        reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"),
        data=payload,
    )

    assert response.status_code == expected_status
    if expected_redirect:
        assert response.url == reverse("admin:imager_raspberrypiimageartifact_changelist")
    else:
        assert not hasattr(response, "url")

    assert mock_build.called is build_called
    if build_called:
        kwargs = mock_build.call_args.kwargs
        assert kwargs["name"] == payload.get("name")
        assert kwargs["output_dir"] == Path(settings.BASE_DIR) / "build/rpi-imager"
        assert kwargs["customize"] is customize_value

    if expected_message:
        if build_side_effect is not None:
            flashed_messages = [str(message) for message in get_messages(response.wsgi_request)]
            assert expected_message in flashed_messages
        else:
            assert expected_message in response.content.decode("utf-8")


@pytest.mark.django_db
@patch("apps.imager.admin.build_rpi4b_image")
def test_imager_admin_create_rpi_image_view_rejects_disallowed_paths(mock_build, admin_client) -> None:
    """Regression: admin form should reject path traversal input for local paths."""

    response = admin_client.post(
        reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"),
        data={
            "name": "nightly",
            "base_image_uri": "file:///etc/passwd",
            "output_dir": "../../tmp/pwned",
            "download_base_uri": "",
            "git_url": "https://github.com/arthexis/arthexis.git",
        },
    )

    assert response.status_code == 200
    assert "Base image path is outside allowed image directories." in response.content.decode("utf-8")
    assert "Output directory is outside allowed output directories." in response.content.decode("utf-8")
    mock_build.assert_not_called()
