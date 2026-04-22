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
    BlockDeviceInfo,
    ImagerBuildError,
    _build_download_uri,
    _download_remote_base_image,
    _resolve_root_disk_path,
    _validate_remote_base_image_url,
    build_rpi4b_image,
    list_block_devices,
    write_image_to_device,
)


def test_list_block_devices_requests_tree_output_for_partition_mountpoints() -> None:
    """Regression: lsblk JSON discovery should request tree mode for children[]."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"children":[{"path":"/dev/sdb1","mountpoints":["/media/card"]}]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=1, stdout="", stderr="")

    with patch("apps.imager.services.subprocess.run", side_effect=[lsblk_result, root_findmnt]) as run_mock:
        devices = list_block_devices()

    assert devices[0].mountpoints == ["/media/card"]
    assert run_mock.call_args_list[0].args[0] == [
        "lsblk",
        "-J",
        "-b",
        "--tree",
        "-o",
        "PATH,SIZE,RM,TRAN,TYPE,MOUNTPOINTS",
    ]


def test_list_block_devices_collects_mountpoints_from_nested_descendants() -> None:
    """Regression: nested children mountpoints must prevent in-use target writes."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"children":[{"path":"/dev/sdb1","mountpoints":[null],"children":[{"path":"/dev/mapper/crypt","mountpoints":["/media/card"]}]}]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=1, stdout="", stderr="")

    with patch("apps.imager.services.subprocess.run", side_effect=[lsblk_result, root_findmnt]):
        devices = list_block_devices()

    assert devices[0].mountpoints == ["/media/card"]
    assert devices[0].partitions == ["/dev/sdb1", "/dev/mapper/crypt"]


def test_list_block_devices_raises_operator_error_when_lsblk_missing() -> None:
    """Regression: operators should get a clear error if lsblk is unavailable."""

    with (
        patch("apps.imager.services.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(ImagerBuildError, match="lsblk"),
    ):
        list_block_devices()


def test_resolve_root_disk_path_returns_none_when_required_tools_missing() -> None:
    """Regression: root-disk discovery should gracefully handle missing host tools."""

    with patch("apps.imager.services.subprocess.run", side_effect=FileNotFoundError):
        assert _resolve_root_disk_path() is None


def test_resolve_root_disk_path_walks_to_disk_parent() -> None:
    """Regression: root-disk detection should resolve parent chains to disk devices."""

    findmnt_result = SimpleNamespace(returncode=0, stdout="/dev/mapper/vg-root\n", stderr="")
    mapper_info = SimpleNamespace(returncode=0, stdout="lvm dm-0\n", stderr="")
    dm_info = SimpleNamespace(returncode=0, stdout="part nvme0n1\n", stderr="")
    disk_info = SimpleNamespace(returncode=0, stdout="disk\n", stderr="")

    with patch(
        "apps.imager.services.subprocess.run",
        side_effect=[findmnt_result, mapper_info, dm_info, disk_info],
    ):
        root_disk = _resolve_root_disk_path()

    assert root_disk == "/dev/nvme0n1"


@pytest.mark.django_db
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
            "build_engine": "arthexis-bootstrap",
            "build_profile": "bootstrap",
            "profile_manifest": {},
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
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs["build_engine"] == "arthexis-bootstrap"
    assert mock_build.call_args.kwargs["profile"] == "bootstrap"


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_passes_connect_ota_profile_metadata(mock_build, tmp_path: Path) -> None:
    """Regression: build command should pass selected engine/profile metadata to backend."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    mock_build.return_value = type(
        "BuildResult",
        (),
        {
            "output_path": output_path,
            "sha256": "abc123",
            "size_bytes": 2,
            "download_uri": "",
            "build_engine": "arthexis-bootstrap",
            "build_profile": "connect-ota",
            "profile_manifest": {},
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "ota-v1",
        "--base-image-uri",
        str(output_path),
        "--profile",
        "connect-ota",
        "--profile-metadata",
        '{"release_version":"2026.04.0","compatibility_model":"pi4","compatibility_board":"rpi-4b","ota_channel":"stable","ota_artifact_type":"raw-disk-image","required_artifacts":["connect-ota-agent","connect-ota-channel-config","connect-ota-device-identity"]}',
    )

    assert mock_build.call_args.kwargs["profile"] == "connect-ota"
    assert mock_build.call_args.kwargs["profile_metadata"]["ota_channel"] == "stable"


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
def test_build_rpi4b_image_persists_connect_ota_engine_profile_metadata(tmp_path: Path) -> None:
    """Regression: connect-ota profile metadata must persist for rollout eligibility checks."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    profile_metadata = {
        "base_os": "raspberry-pi-os-trixie",
        "architecture": "arm64",
        "release_version": "2026.04.0",
        "compatibility_model": "raspberry-pi-4",
        "compatibility_board": "rpi-4b",
        "ota_channel": "stable",
        "ota_artifact_type": "raw-disk-image",
        "required_artifacts": [
            "connect-ota-agent",
            "connect-ota-channel-config",
            "connect-ota-device-identity",
        ],
    }

    with patch("apps.imager.services._customize_image"):
        build_rpi4b_image(
            name="connect-stable",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            profile="connect-ota",
            profile_metadata=profile_metadata,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="connect-stable")
    assert artifact.build_engine == "arthexis-bootstrap"
    assert artifact.build_profile == "connect-ota"
    assert artifact.metadata["profile_manifest"]["compatibility_model"] == "raspberry-pi-4"


@pytest.mark.django_db
def test_build_rpi4b_image_rejects_connect_ota_profile_when_manifest_fields_missing(tmp_path: Path) -> None:
    """Regression: connect-ota profile should reject missing rollout manifest requirements."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="requires manifest fields"):
        build_rpi4b_image(
            name="connect-invalid",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
            profile="connect-ota",
            profile_metadata={
                "base_os": "raspberry-pi-os-trixie",
                "architecture": "arm64",
                "release_version": "2026.04.0",
                "required_artifacts": [
                    "connect-ota-agent",
                    "connect-ota-channel-config",
                    "connect-ota-device-identity",
                ],
            },
        )


@pytest.mark.django_db
def test_build_rpi4b_image_rejects_connect_ota_profile_when_base_metadata_missing(tmp_path: Path) -> None:
    """Regression: connect-ota profile must validate explicit source base metadata."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="requires base_os"):
        build_rpi4b_image(
            name="connect-missing-base",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
            profile="connect-ota",
            profile_metadata={
                "release_version": "2026.04.0",
                "compatibility_model": "raspberry-pi-4",
                "compatibility_board": "rpi-4b",
                "ota_channel": "stable",
                "ota_artifact_type": "raw-disk-image",
                "required_artifacts": [
                    "connect-ota-agent",
                    "connect-ota-channel-config",
                    "connect-ota-device-identity",
                ],
            },
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


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
@patch("apps.imager.services.os.fsync")
def test_write_image_to_device_fsyncs_target_before_verification(
    fsync_mock, list_devices_mock, tmp_path: Path
) -> None:
    """Regression: write path should fsync target media before checksum verification."""

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

    write_image_to_device(
        device_path=str(target),
        image_path=str(source),
        confirmed=True,
    )

    fsync_mock.assert_called_once()
