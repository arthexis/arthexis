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
    BlockDeviceInfo,
    TARGET_RPI4B,
    ImagerBuildError,
    _build_download_uri,
    _download_remote_base_image,
    _validate_remote_base_image_url,
    build_rpi4b_image,
    write_image_to_device,
)


@pytest.mark.django_db
@pytest.mark.integration
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_prints_metadata(mock_build, tmp_path: Path) -> None:
    """Regression: imager build should print generated artifact metadata."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    mock_build.return_value = type(
        "BuildResult",
        (),
        {
            "output_path": output_path,
            "sha256": "abc123",
            "size_bytes": 2,
            "download_uri": "https://downloads.example.com/artifact.img",
        },
    )()

    out = StringIO()
    call_command(
        "imager",
        "build",
        "--name",
        "v0-5-0",
        "--base-image-uri",
        str(output_path),
        stdout=out,
    )

    output = out.getvalue()
    assert "Built image:" in output
    assert "sha256=abc123" in output
    assert "download_uri=https://downloads.example.com/artifact.img" in output


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


@patch("apps.imager.management.commands.imager.list_block_devices")
def test_imager_devices_command_lists_discovery_metadata(list_devices_mock) -> None:
    """Regression: devices action should print block safety metadata."""

    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path="/dev/sda",
            size_bytes=64000000000,
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=["/dev/sda1"],
            protected=False,
        )
    ]

    out = StringIO()
    call_command("imager", "devices", stdout=out)
    output = out.getvalue()

    assert "/dev/sda" in output
    assert "removable=yes" in output
    assert "protected=no" in output


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_refuses_protected_disk(list_devices_mock, tmp_path: Path) -> None:
    """Regression: write should fail when target disk is marked protected."""

    source = tmp_path / "source.img"
    source.write_bytes(b"safe")
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path="/dev/sda",
            size_bytes=1024 * 1024,
            transport="nvme",
            removable=False,
            mountpoints=[],
            partitions=[],
            protected=True,
        )
    ]

    with pytest.raises(ImagerBuildError, match="protected system/root disk"):
        write_image_to_device(device_path="/dev/sda", image_path=str(source), confirmed=True)


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_refuses_mounted_target(list_devices_mock, tmp_path: Path) -> None:
    """Regression: mounted targets should be rejected before write."""

    source = tmp_path / "source.img"
    source.write_bytes(b"safe")
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path="/dev/sdb",
            size_bytes=1024 * 1024,
            transport="usb",
            removable=True,
            mountpoints=["/media/card"],
            partitions=["/dev/sdb1"],
            protected=False,
        )
    ]

    with pytest.raises(ImagerBuildError, match="Unmount all partitions first"):
        write_image_to_device(device_path="/dev/sdb", image_path=str(source), confirmed=True)


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_refuses_undersized_target(list_devices_mock, tmp_path: Path) -> None:
    """Regression: write should fail when target capacity is smaller than image."""

    source = tmp_path / "source.img"
    source.write_bytes(b"12345")
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path="/dev/sdb",
            size_bytes=4,
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=[],
            protected=False,
        )
    ]

    with pytest.raises(ImagerBuildError, match="is too small"):
        write_image_to_device(device_path="/dev/sdb", image_path=str(source), confirmed=True)


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_writes_and_verifies_and_updates_artifact_metadata(
    list_devices_mock, tmp_path: Path
) -> None:
    """Regression: write should copy bytes, verify checksum, and persist artifact write metadata."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"artifact-bytes")
    target = tmp_path / "device.bin"
    target.write_bytes(b"\0" * 32)
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path=str(target),
            size_bytes=32,
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=[],
            protected=False,
        )
    ]
    artifact = RaspberryPiImageArtifact.objects.create(
        name="stable",
        target=TARGET_RPI4B,
        base_image_uri=str(source),
        output_filename=source.name,
        output_path=str(source),
        sha256="",
        size_bytes=source.stat().st_size,
        download_uri="",
        metadata={},
    )

    result = write_image_to_device(
        device_path=str(target),
        artifact_name="stable",
        confirmed=True,
    )

    artifact.refresh_from_db()
    assert target.read_bytes()[: source.stat().st_size] == source.read_bytes()
    assert result.verified is True
    assert artifact.metadata["last_write"]["device_path"] == str(target)
