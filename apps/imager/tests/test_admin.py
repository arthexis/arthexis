"""Regression tests for Raspberry Pi imager admin UI actions."""

from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from django.contrib import admin
from django.test import override_settings
from django.urls import reverse

from apps.imager.admin import _probe_download_url
from apps.imager.models import RaspberryPiImageArtifact


@pytest.mark.django_db
def test_imager_admin_has_no_duplicate_dashboard_create_action():
    """Regression: dashboard action wiring should not duplicate the create-image shortcut."""

    model_admin = admin.site._registry[RaspberryPiImageArtifact]

    assert not hasattr(model_admin, "dashboard_actions")
    assert not hasattr(model_admin, "create_rpi_image_dashboard_action")


class _ProbeResponse:
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    @staticmethod
    def getcode():
        return 200

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

@patch("apps.imager.admin.build_rpi4b_image")
@override_settings(
    IMAGER_ADMIN_BASE_IMAGE_ALLOWED_ROOTS=("/tmp",),
    IMAGER_ADMIN_OUTPUT_ALLOWED_ROOTS=("/tmp",),
)
def test_imager_admin_create_rpi_image_view_shows_artifact_download_actions(mock_build, admin_client, tmp_path):
    """Regression: successful builds should return to the wizard with artifact URL actions."""

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output_path = output_dir / "stable-rpi-4b.img"
    output_path.write_bytes(b"artifact")

    artifact = RaspberryPiImageArtifact.objects.create(
        name="stable",
        target="rpi-4b",
        base_image_uri=str(tmp_path / "base.img"),
        output_filename=output_path.name,
        output_path=str(output_path),
        sha256="abc123",
        size_bytes=8,
        download_uri="https://cdn.example.com/images/stable-rpi-4b.img",
        metadata={},
    )

    mock_build.return_value = type("BuildResult", (), {"output_path": output_path})()

    response = admin_client.post(
        reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"),
        data={
            "name": "stable",
            "base_image_uri": str(tmp_path / "base.img"),
            "output_dir": str(output_dir),
            "download_base_uri": "https://cdn.example.com/images",
            "git_url": "https://github.com/arthexis/arthexis.git",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert any(f"?artifact={artifact.pk}" in url for url, _status in response.redirect_chain)
    body = response.content.decode("utf-8")
    assert "Latest build artifact" in body
    assert "Test URL" in body
    assert artifact.download_uri in body

@pytest.mark.parametrize(
    ("download_url", "expected_message"),
    [
        ("file:///tmp/stable-rpi-4b.img", "Unsupported download URL."),
        ("http://127.0.0.1/admin", "Refusing to probe local or private addresses."),
    ],
)
def test_probe_download_url_blocks_unsafe_targets(download_url, expected_message):
    """Regression: URL probing should reject unsupported schemes and private hosts."""

    reachable, result = _probe_download_url(download_url)
    assert reachable is False
    assert result == expected_message

@patch("apps.imager.admin.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))])
@patch("apps.imager.admin.build_opener")
def test_probe_download_url_revalidates_redirect_targets(build_opener_mock, _getaddrinfo_mock):
    """Regression: redirect responses must not allow probes to private hosts."""

    build_opener_mock.return_value.open.side_effect = [
        HTTPError(
            "https://cdn.example.com/images/stable-rpi-4b.img",
            302,
            "Found",
            {"Location": "http://127.0.0.1/secret"},
            None,
        )
    ]

    reachable, result = _probe_download_url("https://cdn.example.com/images/stable-rpi-4b.img")

    assert reachable is False
    assert result == "Refusing to probe local or private addresses."

@patch("apps.imager.admin.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))])
@patch("apps.imager.admin.build_opener")
def test_probe_download_url_allows_five_redirect_hops(build_opener_mock, _getaddrinfo_mock):
    """Regression: redirect limit should allow five redirects before failing."""

    build_opener_mock.return_value.open.side_effect = [
        HTTPError("https://cdn.example.com/images/stable.img", 302, "Found", {"Location": "/hop-1"}, None),
        HTTPError("https://cdn.example.com/hop-1", 302, "Found", {"Location": "/hop-2"}, None),
        HTTPError("https://cdn.example.com/hop-2", 302, "Found", {"Location": "/hop-3"}, None),
        HTTPError("https://cdn.example.com/hop-3", 302, "Found", {"Location": "/hop-4"}, None),
        HTTPError("https://cdn.example.com/hop-4", 302, "Found", {"Location": "/hop-5"}, None),
        _ProbeResponse(),
    ]

    reachable, result = _probe_download_url("https://cdn.example.com/images/stable.img")

    assert reachable is True
    assert result == "HTTP 200"

@patch("apps.imager.admin.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))])
@patch("apps.imager.admin.build_opener")
def test_probe_download_url_fails_after_sixth_redirect(build_opener_mock, _getaddrinfo_mock):
    """Regression: sixth redirect hop should fail with an explicit limit error."""

    build_opener_mock.return_value.open.side_effect = [
        HTTPError("https://cdn.example.com/images/stable.img", 302, "Found", {"Location": "/hop-1"}, None),
        HTTPError("https://cdn.example.com/hop-1", 302, "Found", {"Location": "/hop-2"}, None),
        HTTPError("https://cdn.example.com/hop-2", 302, "Found", {"Location": "/hop-3"}, None),
        HTTPError("https://cdn.example.com/hop-3", 302, "Found", {"Location": "/hop-4"}, None),
        HTTPError("https://cdn.example.com/hop-4", 302, "Found", {"Location": "/hop-5"}, None),
        HTTPError("https://cdn.example.com/hop-5", 302, "Found", {"Location": "/hop-6"}, None),
    ]

    reachable, result = _probe_download_url("https://cdn.example.com/images/stable.img")

    assert reachable is False
    assert result == "Too many redirects."

@patch("apps.imager.admin.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))])
@patch("apps.imager.admin.build_opener")
def test_probe_download_url_fails_redirect_without_location(build_opener_mock, _getaddrinfo_mock):
    """Regression: redirects without Location should fail probing."""

    build_opener_mock.return_value.open.side_effect = [
        HTTPError("https://cdn.example.com/images/stable.img", 302, "Found", {}, None),
    ]

    reachable, result = _probe_download_url("https://cdn.example.com/images/stable.img")

    assert reachable is False
    assert result == "HTTP 302"
