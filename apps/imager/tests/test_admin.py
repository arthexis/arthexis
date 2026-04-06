"""Regression tests for Raspberry Pi imager admin UI actions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.imager.models import RaspberryPiImageArtifact


@pytest.mark.django_db
@pytest.mark.integration
@override_settings(
    IMAGER_ADMIN_BASE_IMAGE_ALLOWED_ROOTS=("/tmp/arthexis-imager-tests/base-roots",),
    IMAGER_ADMIN_OUTPUT_ALLOWED_ROOTS=("/tmp/arthexis-imager-tests/output-roots",),
)
@patch("apps.imager.admin.build_rpi4b_image")
@pytest.mark.parametrize("invalid_name", ["../../tmp/pwned", "nested/image"])
def test_imager_admin_create_rpi_image_view_rejects_disallowed_paths(
    mock_build,
    admin_client,
    invalid_name,
) -> None:
    """Regression: admin form should reject path traversal input for local paths and names."""

    response = admin_client.post(
        reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"),
        data={
            "name": invalid_name,
            "base_image_uri": "file:///etc/passwd",
            "output_dir": "../../tmp/pwned",
            "download_base_uri": "",
            "git_url": "https://github.com/arthexis/arthexis.git",
        },
    )

    assert response.status_code == 200
    assert (
        "Artifact name must not contain path separators or traversal segments."
        in response.content.decode("utf-8")
    )
    assert (
        "Base image path is outside allowed image directories."
        in response.content.decode("utf-8")
    )
    assert (
        "Output directory is outside allowed output directories."
        in response.content.decode("utf-8")
    )
    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.admin.build_rpi4b_image")
def test_imager_admin_create_rpi_image_view_redirects_to_wizard_result(
    mock_build,
    admin_client,
) -> None:
    """Regression: successful builds should return to the wizard with artifact details."""

    artifact = RaspberryPiImageArtifact.objects.create(
        name="stable",
        target="rpi-4b",
        base_image_uri="https://example.com/base.img",
        output_filename="stable-rpi-4b.img",
        output_path="build/rpi-imager/stable-rpi-4b.img",
        sha256="a" * 64,
        size_bytes=128,
        download_uri="https://downloads.example.com/stable-rpi-4b.img",
    )
    mock_build.return_value = SimpleNamespace(name=artifact.name)

    response = admin_client.post(
        reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"),
        data={
            "wizard_action": "build",
            "name": artifact.name,
            "base_image_uri": "https://example.com/base.img",
            "output_dir": "build/rpi-imager",
            "download_base_uri": "https://downloads.example.com",
            "git_url": "https://github.com/arthexis/arthexis.git",
        },
    )

    assert response.status_code == 302
    assert (
        response.url
        == f"{reverse('admin:imager_raspberrypiimageartifact_create_rpi_image')}?artifact={artifact.pk}"
    )


@pytest.mark.django_db
@patch(
    "apps.imager.admin._probe_download_uri", return_value=(True, "URL check succeeded.")
)
def test_imager_admin_create_rpi_image_view_tests_download_url(
    mock_probe,
    admin_client,
    tmp_path: Path,
) -> None:
    """Regression: wizard test action should validate the stored download URL."""

    artifact = RaspberryPiImageArtifact.objects.create(
        name="stable",
        target="rpi-4b",
        base_image_uri="https://example.com/base.img",
        output_filename="stable-rpi-4b.img",
        output_path=str(tmp_path / "stable-rpi-4b.img"),
        sha256="b" * 64,
        size_bytes=128,
        download_uri="https://downloads.example.com/stable-rpi-4b.img",
    )

    response = admin_client.post(
        f"{reverse('admin:imager_raspberrypiimageartifact_create_rpi_image')}?artifact={artifact.pk}",
        data={"wizard_action": "test"},
        follow=True,
    )

    assert response.status_code == 200
    assert "URL check succeeded." in response.content.decode("utf-8")
    mock_probe.assert_called_once_with(artifact.download_uri)
