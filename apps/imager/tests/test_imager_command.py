"""Regression tests for Raspberry Pi imager workflows."""

import socket
from contextlib import nullcontext
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import (
    TARGET_RPI4B,
    ImagerBuildError,
    _build_download_uri,
    _download_remote_base_image,
    _validate_remote_base_image_url,
    build_rpi4b_image,
)


@pytest.mark.django_db
def test_build_rpi4b_image_creates_artifact_with_download_uri(tmp_path: Path) -> None:
    """Regression: building an artifact should persist checksum and URI metadata."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with patch("apps.imager.services._customize_image"):
        result = build_rpi4b_image(
            name="stable",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="https://cdn.example.com/images",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="stable")
    assert result.output_path.exists()
    assert artifact.sha256 == result.sha256
    assert artifact.download_uri == "https://cdn.example.com/images/stable-rpi-4b.img"

@pytest.mark.django_db
def test_build_rpi4b_image_rejects_unsafe_artifact_name(tmp_path: Path) -> None:
    """Regression: artifact names should not include path traversal or separators."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Artifact name must start"):
        build_rpi4b_image(
            name="../outside",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )

@pytest.mark.django_db
@patch("apps.imager.services._download_remote_base_image")
def test_build_rpi4b_image_downloads_percent_encoded_http_source(
    download_mock, tmp_path: Path
) -> None:
    """Regression: encoded HTTP paths should download and produce a valid artifact."""

    source_bytes = b"http-image"

    def write_download(uri: str, destination: Path) -> None:
        assert uri == "https://example.com/Raspberry%20Pi%20OS.img"
        destination.write_bytes(source_bytes)

    download_mock.side_effect = write_download

    with patch("apps.imager.services._customize_image"):
        result = build_rpi4b_image(
            name="httpstable",
            base_image_uri="https://example.com/Raspberry%20Pi%20OS.img",
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
        )

    assert result.output_path.exists()
    assert result.output_path.read_bytes() == source_bytes

@pytest.mark.django_db
@override_settings(IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS=True)
@patch("apps.imager.services.socket.getaddrinfo")
def test_build_rpi4b_image_blocks_private_remote_host(getaddrinfo_mock, tmp_path: Path) -> None:
    """Regression: private/internal resolved addresses should be rejected before download."""

    getaddrinfo_mock.return_value = [
        (2, 1, 6, "", ("10.0.0.5", 443)),
    ]

    with pytest.raises(ImagerBuildError, match="blocked non-public address"):
        build_rpi4b_image(
            name="blocked-private",
            base_image_uri="https://internal.example.com/rpi.img",
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )

@pytest.mark.django_db
@override_settings(IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS=("updates.example.com",))
@patch("apps.imager.services._download_remote_base_image")
def test_build_rpi4b_image_allows_public_remote_host_in_allowlist(
    download_mock, tmp_path: Path
) -> None:
    """Regression: explicitly allowed public hosts should pass URL policy gate."""

    source_bytes = b"remote-public"

    def write_download(uri: str, destination: Path) -> None:
        assert uri == "https://updates.example.com/rpi.img"
        destination.write_bytes(source_bytes)

    download_mock.side_effect = write_download

    result = build_rpi4b_image(
        name="allowed-public",
        base_image_uri="https://updates.example.com/rpi.img",
        output_dir=tmp_path,
        download_base_uri="",
        git_url="https://github.com/arthexis/arthexis.git",
        customize=False,
    )

    assert result.output_path.exists()
    assert result.output_path.read_bytes() == source_bytes

@pytest.mark.django_db
def test_build_rpi4b_image_rejects_same_source_and_output_path(tmp_path: Path) -> None:
    """Regression: build should fail when source image equals output path."""

    output_path = tmp_path / "stable-rpi-4b.img"
    output_path.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="must differ from output artifact path"):
        build_rpi4b_image(
            name="stable",
            base_image_uri=str(output_path),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
        )

@override_settings(IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS=("internal.example.com",))
@patch("apps.imager.services.socket.getaddrinfo")
def test_validate_remote_base_image_url_allows_private_host_when_allowlisted(getaddrinfo_mock) -> None:
    """Regression: allowlisted hosts should bypass private-address blocking."""

    getaddrinfo_mock.return_value = [(2, 1, 6, "", ("10.0.0.5", 443))]

    _validate_remote_base_image_url("https://internal.example.com/rpi.img")

def test_download_remote_base_image_validates_redirect_target(tmp_path: Path) -> None:
    """Regression: redirect targets should be validated before following."""

    destination = tmp_path / "base.img"
    redirect_response = nullcontext(
        SimpleNamespace(
            getcode=lambda: 302,
            headers={"Location": "https://internal.example.com/image.img"},
        )
    )
    final_response = nullcontext(
        SimpleNamespace(
            getcode=lambda: 200,
            headers={},
            read=BytesIO(b"image").read,
        )
    )

    opener = SimpleNamespace(open=Mock(side_effect=[redirect_response, final_response]))

    with (
        patch("apps.imager.services.build_opener", return_value=opener),
        patch("apps.imager.services._validate_remote_base_image_url") as validate_mock,
    ):
        _download_remote_base_image("https://example.com/image.img", destination)

    assert validate_mock.call_args_list == [
        call("https://example.com/image.img"),
        call("https://internal.example.com/image.img"),
    ]

