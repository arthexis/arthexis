"""Regression tests for Raspberry Pi imager admin UI actions."""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

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
@patch("apps.imager.admin.build_rpi4b_image")
def test_imager_admin_create_rpi_image_view_builds_artifact(mock_build, admin_client, tmp_path: Path) -> None:
    """Regression: create image admin form should call build service and redirect."""

    response = admin_client.post(
        reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"),
        data={
            "name": "nightly",
            "base_image_uri": "/tmp/base.img",
            "output_dir": str(tmp_path),
            "download_base_uri": "https://downloads.example.com/imager",
            "git_url": "https://github.com/arthexis/arthexis.git",
            "skip_customize": "",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("admin:imager_raspberrypiimageartifact_changelist")
    kwargs = mock_build.call_args.kwargs
    assert kwargs["name"] == "nightly"
    assert kwargs["output_dir"] == tmp_path
    assert kwargs["customize"] is True
