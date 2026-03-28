"""Regression tests for Raspberry Pi imager admin UI actions."""

from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import reverse


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
