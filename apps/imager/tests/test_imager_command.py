"""Regression tests for Raspberry Pi imager workflows."""

import lzma
import shlex
import socket
from contextlib import nullcontext
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import (
    TARGET_RPI4B,
    BlockDeviceInfo,
    ImagerBuildError,
    _build_download_uri,
    _customize_image,
    _download_remote_base_image,
    _guestfish_remove_file,
    _guestfish_symlink,
    _guestfish_write,
    _resolve_root_disk_path,
    _validate_remote_base_image_url,
    build_rpi4b_image,
    list_block_devices,
    write_image_to_device,
)

VALID_RECOVERY_KEY_ONE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILOoi93uar4kpDufSrgJPoOKh8UzGiiAsz+GIspRlj7p recovery-one"
VALID_RECOVERY_KEY_TWO = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPxEAcOg5erwB9w67f4eyf3DZiTLQ3sPik4Q6WLTl2XB recovery-two"
MALFORMED_RECOVERY_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAA malformed"


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


def test_list_block_devices_marks_root_mount_disk_protected_when_findmnt_uses_dev_root() -> None:
    """Regression: root disks must stay protected even when findmnt reports /dev/root."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/mmcblk0","size":"64","rm":false,"tran":null,"type":"disk","mountpoints":[null],"children":[{"path":"/dev/mmcblk0p2","mountpoints":["/","/home/arthe"]}]},{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"children":[{"path":"/dev/sdb1","mountpoints":[null]}]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=0, stdout="/dev/root\n", stderr="")
    dev_root_info = SimpleNamespace(returncode=32, stdout="", stderr="not a block device")

    with patch(
        "apps.imager.services.subprocess.run",
        side_effect=[lsblk_result, root_findmnt, dev_root_info],
    ):
        devices = list_block_devices()

    assert devices[0].path == "/dev/mmcblk0"
    assert devices[0].protected is True
    assert devices[1].path == "/dev/sdb"
    assert devices[1].protected is False


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


def test_guestfish_remove_file_uses_architecture_neutral_rm_f(tmp_path: Path) -> None:
    """Regression: stale-file cleanup should not depend on guest /bin/sh architecture."""

    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("apps.imager.services.subprocess.run", return_value=result) as run_mock:
        _guestfish_remove_file(image_path, "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf")

    assert run_mock.call_args.kwargs["input"] == (
        "rm-f /etc/ssh/sshd_config.d/20-arthexis-recovery.conf\n"
    )
    env = run_mock.call_args.kwargs["env"]
    assert env["TMPDIR"].startswith(str(tmp_path))
    assert env["LIBGUESTFS_TMPDIR"] == env["TMPDIR"]
    assert env["LIBGUESTFS_CACHEDIR"] == str(tmp_path / ".libguestfs-cache")


def test_guestfish_symlink_uses_guestfish_ln_sf(tmp_path: Path) -> None:
    """Regression: systemd enablement should be written as image-native symlinks."""

    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("apps.imager.services.subprocess.run", return_value=result) as run_mock:
        _guestfish_symlink(
            image_path,
            target="/etc/systemd/system/arthexis-recovery-access.service",
            link_path=(
                "/etc/systemd/system/multi-user.target.wants/"
                "arthexis-recovery-access.service"
            ),
        )

    assert run_mock.call_args.kwargs["input"] == (
        "ln-sf /etc/systemd/system/arthexis-recovery-access.service "
        "/etc/systemd/system/multi-user.target.wants/arthexis-recovery-access.service\n"
    )
    env = run_mock.call_args.kwargs["env"]
    assert env["TMPDIR"].startswith(str(tmp_path))
    assert env["LIBGUESTFS_TMPDIR"] == env["TMPDIR"]
    assert env["LIBGUESTFS_CACHEDIR"] == str(tmp_path / ".libguestfs-cache")


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
        "--skip-recovery-ssh",
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
        "--skip-recovery-ssh",
        "--profile",
        "connect-ota",
        "--profile-metadata",
        '{"release_version":"2026.04.0","compatibility_model":"pi4","compatibility_board":"rpi-4b","ota_channel":"stable","ota_artifact_type":"raw-disk-image","required_artifacts":["connect-ota-agent","connect-ota-channel-config","connect-ota-device-identity"]}',
    )

    assert mock_build.call_args.kwargs["profile"] == "connect-ota"
    assert mock_build.call_args.kwargs["profile_metadata"]["ota_channel"] == "stable"


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_reads_recovery_authorized_key_files(mock_build, tmp_path: Path) -> None:
    """Regression: recovery key files should flow into build customization args."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    authorized_key_file = tmp_path / "recovery.pub"
    authorized_key_file.write_text(
        "# comment\n"
        f"{VALID_RECOVERY_KEY_ONE}\n"
        "\n"
        f"{VALID_RECOVERY_KEY_TWO}\n",
        encoding="utf-8",
    )
    mock_build.return_value = type(
        "BuildResult",
        (),
        {
            "output_path": output_path,
            "sha256": "abc123",
            "size_bytes": 2,
            "download_uri": "",
            "build_engine": "arthexis-bootstrap",
            "build_profile": "bootstrap",
            "profile_manifest": {},
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "recovery-v1",
        "--base-image-uri",
        str(output_path),
        "--recovery-authorized-key-file",
        str(authorized_key_file),
    )

    assert mock_build.call_args.kwargs["recovery_ssh_user"] == "arthe"
    assert mock_build.call_args.kwargs["recovery_authorized_keys"] == [
        VALID_RECOVERY_KEY_ONE,
        VALID_RECOVERY_KEY_TWO,
    ]


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_ignores_non_public_key_lines(mock_build, tmp_path: Path) -> None:
    """Regression: recovery key ingestion should ignore malformed and private key lines."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    authorized_key_file = tmp_path / "recovery.pub"
    authorized_key_file.write_text(
        "# comment\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "invalid-content\n"
        f"{MALFORMED_RECOVERY_KEY}\n"
        f"{VALID_RECOVERY_KEY_ONE}\n",
        encoding="utf-8",
    )
    mock_build.return_value = type(
        "BuildResult",
        (),
        {
            "output_path": output_path,
            "sha256": "abc123",
            "size_bytes": 2,
            "download_uri": "",
            "build_engine": "arthexis-bootstrap",
            "build_profile": "bootstrap",
            "profile_manifest": {},
        },
    )()

    stderr = StringIO()
    call_command(
        "imager",
        "build",
        "--name",
        "recovery-v2",
        "--base-image-uri",
        str(output_path),
        "--recovery-authorized-key-file",
        str(authorized_key_file),
        stderr=stderr,
    )

    assert mock_build.call_args.kwargs["recovery_authorized_keys"] == [
        VALID_RECOVERY_KEY_ONE,
    ]
    warnings = stderr.getvalue()
    assert "Skipping unrecognized key line" in warnings
    assert "Skipping malformed public key line" in warnings
    assert str(authorized_key_file) in warnings
    assert "BEGIN OPENSSH PRIVATE KEY" not in warnings
    assert "invalid-content" not in warnings


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_reads_inline_recovery_authorized_keys(mock_build, tmp_path: Path) -> None:
    """Regression: inline recovery key options should be accepted as command params."""

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
            "build_profile": "bootstrap",
            "profile_manifest": {},
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "recovery-inline",
        "--base-image-uri",
        str(output_path),
        "--recovery-authorized-key",
        VALID_RECOVERY_KEY_ONE,
        "--recovery-authorized-key",
        VALID_RECOVERY_KEY_TWO,
    )

    assert mock_build.call_args.kwargs["recovery_ssh_user"] == "arthe"
    assert mock_build.call_args.kwargs["recovery_authorized_keys"] == [
        VALID_RECOVERY_KEY_ONE,
        VALID_RECOVERY_KEY_TWO,
    ]


def test_imager_build_command_reports_non_utf8_recovery_key_file(tmp_path: Path) -> None:
    """Regression: non-UTF8 key files should return a clean command error."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    authorized_key_file = tmp_path / "recovery.pub"
    authorized_key_file.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(CommandError, match="Could not read recovery authorized key file"):
        call_command(
            "imager",
            "build",
            "--name",
            "recovery-binary-key-file",
            "--base-image-uri",
            str(output_path),
            "--recovery-authorized-key-file",
            str(authorized_key_file),
        )


def test_imager_build_command_requires_recovery_ssh_key_by_default(tmp_path: Path) -> None:
    """Regression: customized builds should fail fast unless recovery SSH is explicit."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(CommandError, match="Recovery SSH is required for customized image builds"):
        call_command(
            "imager",
            "build",
            "--name",
            "recovery-required",
            "--base-image-uri",
            str(output_path),
        )


def test_imager_build_command_rejects_skip_recovery_ssh_with_keys(tmp_path: Path) -> None:
    """Regression: skip flag should not allow contradictory key arguments."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(CommandError, match="cannot be combined"):
        call_command(
            "imager",
            "build",
            "--name",
            "recovery-skip-conflict",
            "--base-image-uri",
            str(output_path),
            "--skip-recovery-ssh",
            "--recovery-authorized-key",
            VALID_RECOVERY_KEY_ONE,
        )


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_allows_explicit_skip_recovery_ssh(mock_build, tmp_path: Path) -> None:
    """Regression: operators can intentionally opt out of recovery SSH lane."""

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
            "build_profile": "bootstrap",
            "profile_manifest": {},
        },
    )()

    stdout = StringIO()
    call_command(
        "imager",
        "build",
        "--name",
        "recovery-skip",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        stdout=stdout,
    )

    assert mock_build.call_args.kwargs["recovery_authorized_keys"] == []
    assert mock_build.call_args.kwargs["recovery_ssh_user"] == ""
    assert "recovery_ssh=disabled (--skip-recovery-ssh)" in stdout.getvalue()


def test_customize_image_writes_recovery_ssh_files_when_authorized_keys_provided(
    tmp_path: Path,
) -> None:
    """Regression: recovery customization must enable first-boot SSH access files."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"pi")
    written_files: dict[str, tuple[str, str | None]] = {}
    guestfish_batches: list[list[str]] = []

    def capture_guestfish(
        image_path_arg: Path,
        commands: list[str],
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert error_message
        guestfish_batches.append(commands)
        for command in commands:
            parts = shlex.split(command)
            if parts and parts[0] == "upload":
                written_files[parts[2]] = (Path(parts[1]).read_text(encoding="utf-8"), None)
            elif parts and parts[0] == "chmod":
                content, _mode = written_files[parts[2]]
                written_files[parts[2]] = (content, parts[1])

    recovery_access = type(
        "RecoverySSHAccess",
        (),
        {
            "username": "arthe",
            "authorized_keys": (
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ),
            "enabled": True,
        },
    )()

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch("apps.imager.services._guestfish_run_commands", side_effect=capture_guestfish),
    ):
        _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            recovery_ssh_access=recovery_access,
        )

    assert len(guestfish_batches) == 3
    assert "mkdir-p /etc/systemd/system/multi-user.target.wants" in guestfish_batches[0]
    assert (
        "ln-sf /etc/systemd/system/arthexis-bootstrap.service "
        "/etc/systemd/system/multi-user.target.wants/arthexis-bootstrap.service"
    ) in guestfish_batches[0]
    assert "mkdir-p /usr/local/share/arthexis" in guestfish_batches[1]
    assert (
        "ln-sf /etc/systemd/system/arthexis-recovery-access.service "
        "/etc/systemd/system/multi-user.target.wants/arthexis-recovery-access.service"
    ) in guestfish_batches[1]
    assert "/usr/local/bin/arthexis-bootstrap.sh" in written_files
    assert "/usr/local/bin/arthexis-recovery-access.sh" in written_files
    assert "/usr/local/share/arthexis/recovery_authorized_keys" in written_files
    assert "/etc/systemd/system/arthexis-bootstrap.service" in written_files
    assert "/etc/systemd/system/arthexis-recovery-access.service" in written_files
    assert "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf" in written_files
    assert "/boot/firstrun.sh" in written_files

    bootstrap_script, bootstrap_mode = written_files["/usr/local/bin/arthexis-bootstrap.sh"]
    recovery_script, recovery_mode = written_files["/usr/local/bin/arthexis-recovery-access.sh"]
    recovery_keys, keys_mode = written_files["/usr/local/share/arthexis/recovery_authorized_keys"]
    recovery_service, recovery_service_mode = written_files[
        "/etc/systemd/system/arthexis-recovery-access.service"
    ]
    firstrun_script, _firstrun_mode = written_files["/boot/firstrun.sh"]
    sshd_config, sshd_mode = written_files["/etc/ssh/sshd_config.d/20-arthexis-recovery.conf"]

    assert bootstrap_mode == "0755"
    assert "missing_packages+=(git ca-certificates)" in bootstrap_script
    apt_update_retry = "apt-get update || { sleep 10; apt-get update; }"
    assert apt_update_retry in bootstrap_script
    assert "apt-get install -y --no-install-recommends" in bootstrap_script
    assert bootstrap_script.index(apt_update_retry) < bootstrap_script.index("apt-get install")
    assert bootstrap_script.index("apt-get install") < bootstrap_script.index("git clone")
    assert recovery_mode == "0755"
    assert keys_mode == "0600"
    assert sshd_mode == "0644"
    assert recovery_service_mode == "0644"
    assert "RECOVERY_USER=arthe" in recovery_script
    assert "NOPASSWD:ALL" in recovery_script
    assert "systemctl enable ssh" in recovery_script
    assert "systemctl restart ssh" not in recovery_script
    assert "Before=ssh.service sshd.service arthexis-bootstrap.service" in recovery_service
    assert "ExecStart=/usr/local/bin/arthexis-recovery-access.sh" in recovery_service
    assert recovery_keys == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery\n"
    assert "/usr/local/bin/arthexis-recovery-access.sh" in firstrun_script
    assert "arthexis-recovery-access.sh failed; continuing with bootstrap" in firstrun_script
    assert "PasswordAuthentication no" in sshd_config


def test_customize_image_does_not_add_recovery_boot_hook_when_recovery_is_disabled(
    tmp_path: Path,
) -> None:
    """Regression: first-boot recovery hook should be gated by explicit recovery settings."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"pi")
    written_files: dict[str, tuple[str, str | None]] = {}
    guestfish_batches: list[list[str]] = []

    def capture_guestfish(
        image_path_arg: Path,
        commands: list[str],
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert error_message
        guestfish_batches.append(commands)
        for command in commands:
            parts = shlex.split(command)
            if parts and parts[0] == "upload":
                written_files[parts[2]] = (Path(parts[1]).read_text(encoding="utf-8"), None)
            elif parts and parts[0] == "chmod":
                content, _mode = written_files[parts[2]]
                written_files[parts[2]] = (content, parts[1])

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch("apps.imager.services._guestfish_run_commands", side_effect=capture_guestfish),
    ):
        _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            recovery_ssh_access=None,
        )

    firstrun_script, _firstrun_mode = written_files["/boot/firstrun.sh"]
    assert "/usr/local/bin/arthexis-recovery-access.sh" not in firstrun_script
    assert "/etc/systemd/system/arthexis-bootstrap.service" in written_files
    assert "/etc/systemd/system/arthexis-recovery-access.service" not in written_files
    assert len(guestfish_batches) == 3
    assert "mkdir-p /etc/systemd/system/multi-user.target.wants" in guestfish_batches[0]
    assert (
        "ln-sf /etc/systemd/system/arthexis-bootstrap.service "
        "/etc/systemd/system/multi-user.target.wants/arthexis-bootstrap.service"
    ) in guestfish_batches[0]
    assert guestfish_batches[1] == [
        "rm-f /usr/local/share/arthexis/recovery_authorized_keys",
        "rm-f /usr/local/bin/arthexis-recovery-access.sh",
        "rm-f /etc/ssh/sshd_config.d/20-arthexis-recovery.conf",
        "rm-f /etc/systemd/system/arthexis-recovery-access.service",
        (
            "rm-f /etc/systemd/system/multi-user.target.wants/"
            "arthexis-recovery-access.service"
        ),
        "rm-f /etc/sudoers.d/90-arthexis-recovery",
    ]


def test_build_rpi4b_image_rejects_invalid_recovery_ssh_username(tmp_path: Path) -> None:
    """Regression: recovery SSH usernames must be Linux-safe for first-boot scripting."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Invalid recovery SSH username"):
        build_rpi4b_image(
            name="recovery-invalid-user",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
            recovery_ssh_user="arthe;touch /tmp/pwned",
            recovery_authorized_keys=[
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ],
        )


def test_build_rpi4b_image_rejects_recovery_ssh_when_customize_is_disabled(tmp_path: Path) -> None:
    """Regression: recovery SSH options must not be accepted for skip-customize builds."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="requires image customization"):
        build_rpi4b_image(
            name="recovery-no-customize",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
            recovery_authorized_keys=[
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ],
        )


def test_build_rpi4b_image_rejects_recovery_username_without_keys(tmp_path: Path) -> None:
    """Regression: explicit recovery usernames without keys should fail fast."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Recovery SSH user was provided without recovery authorized keys"):
        build_rpi4b_image(
            name="recovery-user-without-keys",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            recovery_ssh_user="fieldops",
            recovery_authorized_keys=[],
        )


def test_build_rpi4b_image_rejects_default_recovery_username_without_keys(tmp_path: Path) -> None:
    """Regression: explicitly supplied default recovery user without keys should fail fast."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Recovery SSH user was provided without recovery authorized keys"):
        build_rpi4b_image(
            name="recovery-default-user-without-keys",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            recovery_ssh_user="arthe",
            recovery_authorized_keys=[],
        )


def test_build_rpi4b_image_rejects_root_recovery_username(tmp_path: Path) -> None:
    """Regression: root must not be accepted as a recovery SSH username."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Invalid recovery SSH username"):
        build_rpi4b_image(
            name="recovery-root-user",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
            recovery_ssh_user="root",
            recovery_authorized_keys=[
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ],
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
    assert artifact.metadata["recovery_ssh"] == {
        "enabled": False,
        "user": "",
        "authorized_key_count": 0,
    }


@pytest.mark.django_db
def test_build_rpi4b_image_persists_recovery_ssh_metadata(tmp_path: Path) -> None:
    """Regression: recovery SSH settings should persist in artifact metadata."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with patch("apps.imager.services._customize_image"):
        build_rpi4b_image(
            name="recovery-enabled",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            recovery_authorized_keys=[
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ],
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="recovery-enabled")
    assert artifact.metadata["recovery_ssh"] == {
        "enabled": True,
        "user": "arthe",
        "authorized_key_count": 1,
    }


@pytest.mark.django_db
def test_build_rpi4b_image_decompresses_local_xz_source(tmp_path: Path) -> None:
    """Regression: .img.xz sources should expand automatically before build copy."""

    source_bytes = b"raspberrypi"
    compressed_source = tmp_path / "base.img.xz"
    with lzma.open(compressed_source, "wb") as handle:
        handle.write(source_bytes)

    with patch("apps.imager.services._customize_image"):
        result = build_rpi4b_image(
            name="stable-xz",
            base_image_uri=str(compressed_source),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
        )

    assert result.output_path.read_bytes() == source_bytes


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
@patch("apps.imager.services._download_remote_base_image")
def test_build_rpi4b_image_downloads_and_decompresses_remote_xz_source(
    download_mock, tmp_path: Path
) -> None:
    """Regression: downloaded .img.xz sources should expand automatically before copy."""

    source_bytes = b"http-image-xz"

    def write_download(uri: str, destination: Path) -> None:
        assert uri == "https://example.com/Raspberry%20Pi%20OS.img.xz"
        with lzma.open(destination, "wb") as handle:
            handle.write(source_bytes)

    download_mock.side_effect = write_download

    with patch("apps.imager.services._customize_image"):
        result = build_rpi4b_image(
            name="httpstable-xz",
            base_image_uri="https://example.com/Raspberry%20Pi%20OS.img.xz",
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


def test_guestfish_write_scopes_temp_dirs_to_image_output_directory(tmp_path: Path) -> None:
    """Regression: guestfish temp dir should be scoped and cleaned while cache persists."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"img")
    local_path = tmp_path / "bootstrap.sh"
    local_path.write_text("#!/bin/sh\n", encoding="utf-8")
    guestfish_result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("apps.imager.services.subprocess.run", return_value=guestfish_result) as run_mock:
        _guestfish_write(image_path, local_path, "/usr/local/bin/arthexis-bootstrap.sh", chmod_mode="0755")

    env = run_mock.call_args.kwargs["env"]
    assert env["TMPDIR"].startswith(str(tmp_path))
    assert env["LIBGUESTFS_TMPDIR"] == env["TMPDIR"]
    assert env["LIBGUESTFS_CACHEDIR"] == str(tmp_path / ".libguestfs-cache")
    assert not Path(env["TMPDIR"]).exists()
    assert (tmp_path / ".libguestfs-cache").is_dir()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("extension", "writer"),
    [
        (".img.xz", lambda path: path.write_bytes(b"not-xz")),
        (".img.gz", lambda path: path.write_bytes(b"not-gzip")),
        (".zip", lambda path: path.write_bytes(b"not-zip")),
    ],
)
def test_build_rpi4b_image_rejects_corrupted_archives(tmp_path: Path, extension: str, writer) -> None:
    """Regression: malformed compressed base images should raise a user-facing build error."""

    compressed_source = tmp_path / f"base{extension}"
    writer(compressed_source)

    with patch("apps.imager.services._customize_image"), pytest.raises(
        ImagerBuildError, match="invalid or corrupted"
    ):
        build_rpi4b_image(
            name=f"corrupt-{extension.replace('.', '-')}",
            base_image_uri=str(compressed_source),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
        )


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
