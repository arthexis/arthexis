"""Regression tests for Raspberry Pi imager workflows."""

import json
import lzma
import os
import shlex
import socket
import subprocess
import tarfile
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.imager.burner import (
    _claim_burn_job,
    _job_progress_heartbeat,
    claim_next_burn_job,
    queue_burn_job,
    run_burn_job,
)
from apps.imager.constants import DEFAULT_ARTHEXIS_GIT_URL
from apps.imager.management.commands.imager import Command as ImagerCommand
from apps.imager.models import RaspberryPiImageArtifact, RaspberryPiImageBurnJob
from apps.imager.reservations import (
    RESERVATION_CLAIM_TOKEN_HASH_KEY,
    ImageReservation,
    RemoteReservationError,
    _fetch_node_info,
    active_parent_network_names,
    plan_image_reservation,
    render_reservation_env,
    watch_reserved_nodes_once,
)
from apps.imager.services import (
    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES,
    IMAGE_SIZE_BYTES_PER_GIB,
    TARGET_RPI4B,
    AccessCheckResult,
    BlockDeviceInfo,
    ImageCustomizationResult,
    ImagerBuildError,
    ImageSizeAdjustment,
    NetworkProfileInfo,
    ServeResult,
    SuiteBundleInfo,
    WriteBackupResult,
    WriteResult,
    _build_download_uri,
    _build_served_artifact_url,
    _create_suite_bundle,
    _customize_image,
    _download_remote_base_image,
    _ensure_image_minimum_size,
    _guestfish_remove_file,
    _guestfish_symlink,
    _guestfish_write,
    _normalize_minimum_image_size_bytes,
    _render_bootstrap_script,
    _resolve_root_disk_path,
    _sanitize_storage_options,
    _should_exclude_suite_bundle_path,
    _validate_remote_base_image_url,
    build_rpi4b_image,
    list_block_devices,
    prepare_image_serve,
    select_host_network_profiles,
    write_image_to_device,
)
from apps.imager.services import (
    test_rpi_access as run_rpi_access_test,
)
from apps.imager.services.build_engine import (
    _guestfish_run_boot_partition_commands,
    _lock_windows_automount_guard_file,
    _validate_initial_profile,
    _windows_automount_guard_lock_path,
)
from apps.nodes.models import Node, NodeRole


def test_imager_build_commands_leave_first_boot_clone_url_explicit():
    parser = ImagerCommand().create_parser("manage.py", "imager")

    build_options = parser.parse_args(
        ["build", "--name", "stable", "--base-image-uri", "base.img"]
    )
    gway_options = parser.parse_args(["gway-burn", "--base-image-uri", "base.img"])

    assert build_options.git_url == DEFAULT_ARTHEXIS_GIT_URL
    assert gway_options.git_url == DEFAULT_ARTHEXIS_GIT_URL


def test_imager_build_accepts_an_initial_toml_profile():
    parser = ImagerCommand().create_parser("manage.py", "imager")

    build_options = parser.parse_args(
        [
            "build",
            "--name",
            "stable",
            "--base-image-uri",
            "base.img",
            "--initial-profile",
            "profiles/gway-004.toml",
        ]
    )

    assert build_options.initial_profile == "profiles/gway-004.toml"


def test_imager_build_accepts_a_connect_auth_toml_profile():
    parser = ImagerCommand().create_parser("manage.py", "imager")

    build_options = parser.parse_args(
        [
            "build",
            "--name",
            "stable",
            "--base-image-uri",
            "base.img",
            "--connect-auth-config",
            "/secure/rpi-connect-auth.toml",
        ]
    )
    gway_options = parser.parse_args(
        [
            "gway-burn",
            "--base-image-uri",
            "base.img",
            "--connect-auth-key-file",
            "/secure/rpi-connect-auth.toml",
        ]
    )

    assert build_options.connect_auth_key_file == "/secure/rpi-connect-auth.toml"
    assert gway_options.connect_auth_key_file == "/secure/rpi-connect-auth.toml"


def test_bootstrap_script_applies_initial_profile_before_starting_services():
    script = _render_bootstrap_script()
    install = 'ARTHEXIS_MIGRATION_POLICY=apply ./install.sh "${install_args[@]}"'
    runtime_migrations = (
        ".venv/bin/python manage.py migrate --noinput\n"
        ".venv/bin/python manage.py migrate --check"
    )
    restore_satellite_policy = (
        'bootstrap_runtime_role="${NODE_ROLE:-Terminal}"\n'
        'case "${bootstrap_runtime_role,,}" in\n'
        "  satellite|watchtower)\n"
        "    printf 'ARTHEXIS_MIGRATION_POLICY=check\n' > \"$APP_HOME/migration.env\"\n"
        "    ;;\n"
        "esac"
    )

    assert "INITIAL_PROFILE=/usr/local/share/arthexis/initial-profile.toml" in script
    assert install in script
    assert runtime_migrations in script
    assert "local start_arg=--no-start" in script
    assert restore_satellite_policy in script
    assert (
        script.index(install)
        < script.index(runtime_migrations)
        < script.index(restore_satellite_policy)
        < script.index('if [ -f "$INITIAL_PROFILE" ]; then', script.index(install))
        < script.index("\nbootstrap_start_app\n", script.index(install))
    )
    assert (
        '.venv/bin/python manage.py imager initial-profile --apply --profile "$INITIAL_PROFILE"'
        in script
    )
    assert "local start_arg=--start" not in script
    assert (
        'manage.py imager initial-profile --apply --profile "$INITIAL_PROFILE"\nfi\nbootstrap_start_app'
        in script
    )
    assert "ARTHEXIS_INITIAL_PROFILE_REQUIRES_NFTABLES=0" in script
    assert (
        'if [ "${ARTHEXIS_INITIAL_PROFILE_REQUIRES_NFTABLES:-0}" = "1" ]; then'
        in script
    )
    assert "add_required_package_if_missing nftables" in script


def test_bootstrap_script_uses_validated_redirect_setting_for_nftables():
    script = _render_bootstrap_script(initial_profile_requires_nftables=True)

    assert "ARTHEXIS_INITIAL_PROFILE_REQUIRES_NFTABLES=1" in script


@pytest.mark.django_db
def test_initial_profile_validation_rejects_invalid_toml_before_build(tmp_path):
    profile = tmp_path / "initial-profile.toml"
    profile.write_text('[rfid\npre_register = ["BAD"]\n', encoding="utf-8")

    with pytest.raises(ImagerBuildError, match="not valid TOML"):
        _validate_initial_profile(profile)


@pytest.mark.django_db
def test_build_uses_node_and_network_settings_from_initial_profile(tmp_path):
    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")
    profile = tmp_path / "initial-profile.toml"
    profile.write_text(
        """[node]
number = 4

[network]
copy_host_profiles = ["Field WiFi"]

[rfid]
pre_register = []
""",
        encoding="utf-8",
    )
    reservation = ImageReservation(
        hostname="gway-004",
        hostname_prefix="gway",
        number=4,
        ipv4_address="10.42.0.4",
        network_cidr="10.42.0.0/16",
        parent_hostname="gway-001",
        claim_token="claim-token",
    )
    customization = ImageCustomizationResult(reservation=reservation)

    with (
        patch(
            "apps.imager.services.plan_image_reservation", return_value=reservation
        ) as reserve_mock,
        patch(
            "apps.imager.services.select_host_network_profiles", return_value=()
        ) as network_mock,
        patch("apps.imager.services._customize_image", return_value=customization),
    ):
        build_rpi4b_image(
            name="profile-settings",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            skip_recovery_ssh=True,
            initial_profile_path=profile,
            minimum_image_size_bytes=0,
        )

    assert reserve_mock.call_args.kwargs["number"] == 4
    assert network_mock.call_args.kwargs["names"] == ["Field WiFi"]


def test_imager_build_commands_accept_explicit_authenticated_clone_url():
    parser = ImagerCommand().create_parser("manage.py", "imager")
    git_url = "git@github.com:arthexis/arthexis.git"

    build_options = parser.parse_args(
        [
            "build",
            "--name",
            "stable",
            "--base-image-uri",
            "base.img",
            "--git-url",
            git_url,
        ]
    )
    gway_options = parser.parse_args(
        ["gway-burn", "--base-image-uri", "base.img", "--git-url", git_url]
    )

    assert build_options.git_url == git_url
    assert gway_options.git_url == git_url


from apps.rpiconnect.models import ConnectImageRelease

VALID_RECOVERY_KEY_ONE = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILOoi93uar4kpDufSrgJPoOKh8UzGiiAsz+GIspRlj7p recovery-one"
VALID_RECOVERY_KEY_TWO = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPxEAcOg5erwB9w67f4eyf3DZiTLQ3sPik4Q6WLTl2XB recovery-two"
MALFORMED_RECOVERY_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAA malformed"


def make_suite_source(tmp_path: Path) -> Path:
    suite_source = tmp_path / "suite"
    suite_source.mkdir()
    for name in ("manage.py", "start.sh", "env-refresh.sh", "command.sh"):
        (suite_source / name).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (suite_source / "apps").mkdir()
    (suite_source / "apps" / "__init__.py").write_text("", encoding="utf-8")
    (suite_source / "db.sqlite3").write_text("secret", encoding="utf-8")
    (suite_source / ".env").write_text("SECRET=1", encoding="utf-8")
    return suite_source


def no_op_image_size_adjustment(
    image_path: Path,
    *,
    minimum_size_bytes: int,
) -> ImageSizeAdjustment:
    """Return image-size metadata without mutating the fake test image."""

    image_size = image_path.stat().st_size
    return ImageSizeAdjustment(
        requested_size_bytes=minimum_size_bytes,
        original_size_bytes=image_size,
        final_size_bytes=image_size,
        image_extended=False,
        root_partition_expanded=minimum_size_bytes > 0,
    )


@patch("apps.imager.services.os.name", "posix")
def test_list_block_devices_requests_tree_output_for_partition_mountpoints() -> None:
    """Regression: lsblk JSON discovery should request tree mode for children[]."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"children":[{"path":"/dev/sdb1","mountpoints":["/media/card"]}]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=1, stdout="", stderr="")

    with patch(
        "apps.imager.services.subprocess.run", side_effect=[lsblk_result, root_findmnt]
    ) as run_mock:
        devices = list_block_devices()

    assert devices[0].mountpoints == ["/media/card"]
    assert run_mock.call_args_list[0].args[0] == [
        "lsblk",
        "-J",
        "-b",
        "--tree",
        "-o",
        "PATH,SIZE,RM,TRAN,TYPE,MOUNTPOINTS,VENDOR,MODEL,SERIAL",
    ]


@patch("apps.imager.services.os.name", "posix")
def test_list_block_devices_includes_stable_identity_paths() -> None:
    """Operators need stable by-id aliases before queueing durable burn jobs."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"serial":"ABC","children":[]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=1, stdout="", stderr="")

    with (
        patch(
            "apps.imager.services.subprocess.run",
            side_effect=[lsblk_result, root_findmnt],
        ),
        patch(
            "apps.imager.services.build_engine._stable_identity_paths_for_device",
            return_value=["/dev/disk/by-id/usb-card-ABC"],
        ),
    ):
        devices = list_block_devices()

    assert devices[0].identity_paths == ["/dev/disk/by-id/usb-card-ABC"]


@patch("apps.imager.services.os.name", "posix")
def test_list_block_devices_blocks_lacie_iamakey_security_media() -> None:
    """Regression: bastion USB keys must never be offered as write targets."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout=(
            '{"blockdevices":[{"path":"/dev/sda","size":"8086618112",'
            '"rm":true,"tran":"usb","type":"disk","mountpoints":[null],'
            '"vendor":"LaCie","model":"iamaKey","serial":"75754c214ff0f2",'
            '"children":[{"path":"/dev/sda1","mountpoints":[null]}]}]}'
        ),
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=1, stdout="", stderr="")

    with patch(
        "apps.imager.services.subprocess.run", side_effect=[lsblk_result, root_findmnt]
    ):
        devices = list_block_devices()

    assert devices[0].vendor == "LaCie"
    assert devices[0].model == "iamaKey"
    assert devices[0].serial == "75754c214ff0f2"
    assert devices[0].write_blocked_reason == (
        "LaCie iamaKey media is reserved for bastion USB unlock keys."
    )


@patch("apps.imager.services.os.name", "posix")
def test_list_block_devices_collects_mountpoints_from_nested_descendants() -> None:
    """Regression: nested children mountpoints must prevent in-use target writes."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"children":[{"path":"/dev/sdb1","mountpoints":[null],"children":[{"path":"/dev/mapper/crypt","mountpoints":["/media/card"]}]}]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=1, stdout="", stderr="")

    with patch(
        "apps.imager.services.subprocess.run", side_effect=[lsblk_result, root_findmnt]
    ):
        devices = list_block_devices()

    assert devices[0].mountpoints == ["/media/card"]
    assert devices[0].partitions == ["/dev/sdb1", "/dev/mapper/crypt"]


@patch("apps.imager.services.os.name", "posix")
def test_list_block_devices_marks_root_mount_disk_protected_when_findmnt_uses_dev_root() -> (
    None
):
    """Regression: root disks must stay protected even when findmnt reports /dev/root."""

    lsblk_result = SimpleNamespace(
        returncode=0,
        stdout='{"blockdevices":[{"path":"/dev/mmcblk0","size":"64","rm":false,"tran":null,"type":"disk","mountpoints":[null],"children":[{"path":"/dev/mmcblk0p2","mountpoints":["/","/home/arthe"]}]},{"path":"/dev/sdb","size":"64","rm":true,"tran":"usb","type":"disk","mountpoints":[null],"children":[{"path":"/dev/sdb1","mountpoints":[null]}]}]}',
        stderr="",
    )
    root_findmnt = SimpleNamespace(returncode=0, stdout="/dev/root\n", stderr="")
    dev_root_info = SimpleNamespace(
        returncode=32, stdout="", stderr="not a block device"
    )

    with patch(
        "apps.imager.services.subprocess.run",
        side_effect=[lsblk_result, root_findmnt, dev_root_info],
    ):
        devices = list_block_devices()

    assert devices[0].path == "/dev/mmcblk0"
    assert devices[0].protected is True
    assert devices[1].path == "/dev/sdb"
    assert devices[1].protected is False


@patch("apps.imager.services.os.name", "posix")
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

    findmnt_result = SimpleNamespace(
        returncode=0, stdout="/dev/mapper/vg-root\n", stderr=""
    )
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
        _guestfish_remove_file(
            image_path, "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf"
        )

    assert run_mock.call_args.kwargs["input"] == (
        "rm-f /etc/ssh/sshd_config.d/20-arthexis-recovery.conf\n"
    )
    env = run_mock.call_args.kwargs["env"]
    assert env["TMPDIR"].startswith(str(tmp_path))
    assert env["LIBGUESTFS_TMPDIR"] == env["TMPDIR"]
    assert env["LIBGUESTFS_CACHEDIR"] == str(tmp_path / ".libguestfs-cache")


def test_guestfish_temp_dirs_are_absolute_for_relative_image_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: supermin rejects relative TMPDIR values from relative output dirs."""

    monkeypatch.chdir(tmp_path)
    output_dir = Path("relative-output")
    output_dir.mkdir()
    image_path = output_dir / "image.img"
    image_path.write_bytes(b"img")
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("apps.imager.services.subprocess.run", return_value=result) as run_mock:
        _guestfish_remove_file(
            image_path, "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf"
        )

    env = run_mock.call_args.kwargs["env"]
    assert Path(env["TMPDIR"]).is_absolute()
    assert Path(env["LIBGUESTFS_TMPDIR"]).is_absolute()
    assert Path(env["LIBGUESTFS_CACHEDIR"]).is_absolute()
    assert env["LIBGUESTFS_TMPDIR"] == env["TMPDIR"]
    assert env["LIBGUESTFS_CACHEDIR"] == str(
        (tmp_path / "relative-output" / ".libguestfs-cache").resolve()
    )


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


def test_guestfish_boot_partition_commands_skip_empty_batch(tmp_path: Path) -> None:
    """Empty boot-partition command batches should not start guestfish."""

    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")

    with patch("apps.imager.services.build_engine._run_guestfish_raw_script") as raw:
        _guestfish_run_boot_partition_commands(
            image_path,
            commands=[],
            error_message="guestfish failed while writing boot partition files",
        )

    raw.assert_not_called()


def test_ensure_image_minimum_size_extends_image_and_expands_rootfs(
    tmp_path: Path,
) -> None:
    """Regression: customized images should not inherit tiny base root filesystems."""

    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")
    scripts: list[str] = []

    def capture_script(path: Path, script: str, *, error_message: str) -> None:
        assert path == image_path
        assert "root filesystem" in error_message
        scripts.append(script)

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch(
            "apps.imager.services._run_guestfish_raw_script", side_effect=capture_script
        ),
    ):
        adjustment = _ensure_image_minimum_size(image_path, minimum_size_bytes=4096)

    assert image_path.stat().st_size == 4096
    assert adjustment == ImageSizeAdjustment(
        requested_size_bytes=4096,
        original_size_bytes=3,
        final_size_bytes=4096,
        image_extended=True,
        root_partition_expanded=True,
    )
    assert scripts == [
        "run\n"
        "part-resize /dev/sda 2 7\n"
        "blockdev-rereadpt /dev/sda\n"
        "e2fsck-f /dev/sda2\n"
        "resize2fs /dev/sda2\n"
    ]


def test_ensure_image_minimum_size_zero_disables_resize(tmp_path: Path) -> None:
    """Operators can opt out when they need a byte-for-byte base-image copy."""

    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")

    with patch("apps.imager.services._run_guestfish_raw_script") as raw_guestfish:
        adjustment = _ensure_image_minimum_size(image_path, minimum_size_bytes=0)

    raw_guestfish.assert_not_called()
    assert image_path.read_bytes() == b"img"
    assert adjustment.root_partition_expanded is False


@pytest.mark.parametrize("minimum_size_bytes", [True, 1.5])
def test_normalize_minimum_image_size_bytes_rejects_non_integral_types(
    minimum_size_bytes: object,
) -> None:
    """Regression: non-integral minimum byte counts should be rejected early."""

    with pytest.raises(ImagerBuildError, match="integer byte count"):
        _normalize_minimum_image_size_bytes(minimum_size_bytes, customize=False)  # type: ignore[arg-type]


def test_ensure_image_minimum_size_resize_only_requires_guestfish(
    tmp_path: Path,
) -> None:
    """Regression: resize-only paths should report guestfish as a resize requirement."""

    image_path = tmp_path / "image.img"
    image_path.write_bytes(b"img")

    with (
        patch("apps.imager.services.shutil.which", return_value=None),
        pytest.raises(ImagerBuildError, match="resize or customize"),
    ):
        _ensure_image_minimum_size(image_path, minimum_size_bytes=4096)


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        (Path(".env.production"), True),
        (Path(".envrc"), True),
        (Path("cache/token.txt"), True),
        (Path("locks/private.lock"), True),
        (Path("env/secret.txt"), True),
        (Path("venv/private.txt"), True),
        (Path("config/.env.local"), True),
        (Path("apps/imager/services.py"), False),
    ],
)
def test_should_exclude_suite_bundle_path_covers_ignored_runtime_paths(
    relative_path: Path, expected: bool
) -> None:
    """Regression: suite bundle filtering should exclude common local runtime/secret paths."""

    assert _should_exclude_suite_bundle_path(relative_path) is expected


def test_create_suite_bundle_requires_command_entrypoint(tmp_path: Path) -> None:
    suite_source = make_suite_source(tmp_path)
    (suite_source / "command.sh").unlink()

    with pytest.raises(ImagerBuildError, match="missing required file: command.sh"):
        _create_suite_bundle(suite_source, tmp_path / "bundle.tar.gz")


def test_create_suite_bundle_marks_bootstrap_entrypoints_executable(
    tmp_path: Path,
) -> None:
    """Regression: Windows-built bundles must boot on Linux after extraction."""

    suite_source = make_suite_source(tmp_path)
    (suite_source / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    bundle_path = tmp_path / "bundle.tar.gz"

    _create_suite_bundle(suite_source, bundle_path)

    with tarfile.open(bundle_path, "r:gz") as archive:
        modes = {
            name: archive.getmember(name).mode
            for name in (
                "command.sh",
                "env-refresh.sh",
                "install.sh",
                "manage.py",
                "start.sh",
            )
        }

    assert all(mode & 0o111 for mode in modes.values())


def test_create_suite_bundle_excludes_private_initial_profile(tmp_path: Path) -> None:
    suite_source = make_suite_source(tmp_path)
    profile = suite_source / "profiles" / "gway-004.toml"
    profile.parent.mkdir()
    profile.write_text("[rfid]\npre_register = []\n", encoding="utf-8")
    bundle_path = tmp_path / "bundle.tar.gz"

    _create_suite_bundle(suite_source, bundle_path, excluded_paths=(profile,))

    with tarfile.open(bundle_path, "r:gz") as archive:
        assert "profiles/gway-004.toml" not in archive.getnames()


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
def test_imager_build_command_passes_connect_ota_profile_metadata(
    mock_build, tmp_path: Path
) -> None:
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
def test_imager_register_connect_release_persists_universal_release() -> None:
    artifact = RaspberryPiImageArtifact.objects.create(
        name="connect-universal-2026.06.06",
        target="rpi-4b",
        base_image_uri="https://example.com/base.img.xz",
        output_filename="connect-universal-2026.06.06.img",
        output_path="/srv/artifacts/connect-universal-2026.06.06.img",
        sha256="a" * 64,
        size_bytes=1024,
        download_uri="https://artifacts.example.test/artifacts/connect-universal.img",
        build_profile="connect-ota",
        metadata={
            "profile_manifest": {
                "base_os": "raspberry-pi-os-trixie",
                "architecture": "arm64",
                "release_version": "2026.06.06",
                "compatibility_model": "raspberry-pi",
                "compatibility_board": "rpi-4b",
                "ota_channel": "stable",
                "ota_artifact_type": "raw-disk-image",
                "required_artifacts": [
                    "connect-ota-agent",
                    "connect-ota-channel-config",
                    "connect-ota-device-identity",
                ],
            }
        },
    )
    out = StringIO()

    call_command(
        "imager",
        "register-connect-release",
        "--artifact",
        artifact.name,
        "--compatibility-tag",
        "bookworm,trixie",
        stdout=out,
    )

    release = ConnectImageRelease.objects.get(name=artifact.name, version="2026.06.06")
    assert release.artifact_url == artifact.download_uri
    assert release.checksum == artifact.sha256
    assert "universal-connect-update" in release.compatibility_tags
    assert "role:watchtower" in release.compatibility_tags
    assert "bookworm" in release.compatibility_tags
    assert release.build_metadata["universal_update"] is True
    assert release.build_metadata["supported_roles"] == [
        "Terminal",
        "Satellite",
        "Control",
        "Watchtower",
    ]
    assert "verification_command=" in out.getvalue()


@pytest.mark.django_db
def test_imager_register_connect_release_rejects_non_connect_profile() -> None:
    artifact = RaspberryPiImageArtifact.objects.create(
        name="bootstrap",
        target="rpi-4b",
        base_image_uri="https://example.com/base.img.xz",
        output_filename="bootstrap.img",
        output_path="/srv/artifacts/bootstrap.img",
        sha256="b" * 64,
        size_bytes=1024,
        download_uri="https://artifacts.example.test/artifacts/bootstrap.img",
        build_profile="bootstrap",
    )

    with pytest.raises(CommandError, match="connect-ota"):
        call_command("imager", "register-connect-release", "--artifact", artifact.name)


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_passes_minimum_image_size(
    mock_build, tmp_path: Path
) -> None:
    """Regression: operators need an explicit headroom override for field images."""

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
        "field",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "10",
    )

    assert (
        mock_build.call_args.kwargs["minimum_image_size_bytes"]
        == 10 * IMAGE_SIZE_BYTES_PER_GIB
    )


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_rejects_negative_minimum_image_size(
    mock_build, tmp_path: Path
) -> None:
    """Regression: negative minimum image size should fail at the command boundary."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(CommandError, match="greater than or equal to zero"):
        call_command(
            "imager",
            "build",
            "--name",
            "field",
            "--base-image-uri",
            str(output_path),
            "--skip-recovery-ssh",
            "--minimum-image-size-gib",
            "-1",
        )
    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_passes_zero_minimum_image_size(
    mock_build, tmp_path: Path
) -> None:
    """Regression: CLI minimum size of zero should pass through unchanged."""

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
        "field",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "0",
    )

    assert mock_build.call_args.kwargs["minimum_image_size_bytes"] == 0


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_passes_bundle_and_host_network_options(
    mock_build, tmp_path: Path
) -> None:
    """Regression: CLI image builds should expose static-suite and network-copy controls."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    suite_source = make_suite_source(tmp_path)
    network_dir = tmp_path / "networks"
    network_dir.mkdir()
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
        "networked",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--suite-source",
        str(suite_source),
        "--copy-host-network",
        "Shop WiFi",
        "--host-network-profile-dir",
        str(network_dir),
    )

    assert mock_build.call_args.kwargs["bundle_suite"] is True
    assert mock_build.call_args.kwargs["suite_source_path"] == suite_source
    assert mock_build.call_args.kwargs["host_network_names"] == ["Shop WiFi"]
    assert mock_build.call_args.kwargs["host_network_profile_dir"] == network_dir


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_resolves_reservation_env_defaults(
    mock_build,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Operators can make reservation and parent-network copying the build default."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    monkeypatch.setenv("IMAGER_RESERVE_DEFAULT", "1")
    monkeypatch.setenv("IMAGER_COPY_PARENT_NETWORK_DEFAULT", "1")
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
            "reservation": {
                "hostname": "gway-004",
                "ipv4_address": "10.42.0.4",
                "node_id": 4,
            },
        },
    )()

    stdout = StringIO()
    call_command(
        "imager",
        "build",
        "--name",
        "reserved",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--reserve-number",
        "4",
        stdout=stdout,
    )

    assert mock_build.call_args.kwargs["reserve_node"] is True
    assert mock_build.call_args.kwargs["reserve_number"] == 4
    assert mock_build.call_args.kwargs["copy_parent_networks"] is True
    assert "reserved_node=gway-004 address=10.42.0.4 id=4" in stdout.getvalue()


@pytest.mark.django_db
@patch(
    "apps.imager.management.commands.imager.format_job_status",
    return_value="job-status",
)
@patch("apps.imager.management.commands.imager.queue_burn_job")
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_uses_manual_number_and_queues_burn(
    mock_build,
    mock_queue_burn,
    _mock_format_status,
    tmp_path: Path,
) -> None:
    """The RFID-facing GWAY command should support manual numbers and burner queueing."""

    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    auth_profile = tmp_path / "rpi-connect-auth.toml"
    auth_profile.write_text('[rpi_connect]\nauth_key = "SECRET"\n', encoding="utf-8")
    auth_profile.chmod(0o600)
    output_path = tmp_path / "gway-007.img"
    output_path.write_bytes(b"artifact")
    mock_build.return_value = SimpleNamespace(
        name="gway-007",
        output_path=output_path,
        sha256="abc123",
        size_bytes=8,
        download_uri="",
        reservation={
            "hostname": "gway-007",
            "ipv4_address": "10.42.0.7",
            "node_id": 7,
        },
    )
    mock_queue_burn.return_value = SimpleNamespace(
        uuid="11111111-1111-1111-1111-111111111111"
    )
    stdout = StringIO()

    call_command(
        "imager",
        "gway-burn",
        "--base-image-uri",
        str(source),
        "--reserve-number",
        "7",
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "0",
        "--connect-auth-config",
        str(auth_profile),
        "--device",
        "/dev/disk/by-id/usb-card",
        stdout=stdout,
    )

    mock_build.assert_called_once()
    build_kwargs = mock_build.call_args.kwargs
    assert build_kwargs["name"] == "gway-007"
    assert build_kwargs["reserve_node"] is True
    assert build_kwargs["reserve_hostname_prefix"] == "gway"
    assert build_kwargs["reserve_number"] == 7
    assert build_kwargs["connect_bootstrap_enabled"] is True
    assert build_kwargs["connect_auth_key_path"] == auth_profile
    assert build_kwargs["downstream_registration_base_url"] == ""
    assert build_kwargs["reservation_claim_token"] == ""
    mock_queue_burn.assert_called_once_with(
        artifact_name="gway-007",
        device_path="/dev/disk/by-id/usb-card",
        backup=False,
        backup_dir="build/rpi-imager/backups",
    )
    output = stdout.getvalue()
    assert "Built GWAY image:" in output
    assert "gway_number=7" in output
    assert "SECRET" not in output
    assert "burn_view=" not in output


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_next_number_env_does_not_set_downstream_registration(
    mock_build,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    output_path = tmp_path / "gway-007.img"
    output_path.write_bytes(b"artifact")
    mock_build.return_value = SimpleNamespace(
        name="gway-007",
        output_path=output_path,
        sha256="abc123",
        size_bytes=8,
        download_uri="",
        reservation={"hostname": "gway-007"},
    )
    monkeypatch.setenv(
        "IMAGER_GWAY_REGISTRATION_BASE_URL",
        "https://registration.example.test",
    )
    monkeypatch.delenv("IMAGER_DOWNSTREAM_REGISTRATION_BASE_URL", raising=False)

    call_command(
        "imager",
        "gway-burn",
        "--base-image-uri",
        str(source),
        "--reserve-number",
        "7",
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "0",
    )

    assert mock_build.call_args.kwargs["downstream_registration_base_url"] == ""


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_downstream_env_sets_downstream_registration(
    mock_build,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    output_path = tmp_path / "gway-007.img"
    output_path.write_bytes(b"artifact")
    mock_build.return_value = SimpleNamespace(
        name="gway-007",
        output_path=output_path,
        sha256="abc123",
        size_bytes=8,
        download_uri="",
        reservation={"hostname": "gway-007"},
    )
    monkeypatch.delenv("IMAGER_GWAY_REGISTRATION_BASE_URL", raising=False)
    monkeypatch.setenv(
        "IMAGER_DOWNSTREAM_REGISTRATION_BASE_URL",
        "https://registration.example.test",
    )

    call_command(
        "imager",
        "gway-burn",
        "--base-image-uri",
        str(source),
        "--reserve-number",
        "7",
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "0",
    )

    assert (
        mock_build.call_args.kwargs["downstream_registration_base_url"]
        == "https://registration.example.test"
    )


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_can_skip_connect_bootstrap(
    mock_build,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    output_path = tmp_path / "gway-007.img"
    output_path.write_bytes(b"artifact")
    mock_build.return_value = SimpleNamespace(
        name="gway-007",
        output_path=output_path,
        sha256="abc123",
        size_bytes=8,
        download_uri="",
        reservation={"hostname": "gway-007"},
    )

    call_command(
        "imager",
        "gway-burn",
        "--base-image-uri",
        str(source),
        "--reserve-number",
        "7",
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "0",
        "--skip-connect-bootstrap",
    )

    assert mock_build.call_args.kwargs["connect_bootstrap_enabled"] is False
    assert mock_build.call_args.kwargs["skip_connect_bootstrap"] is True


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_requires_recovery_ssh_key_by_default(
    mock_build,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")

    with pytest.raises(
        CommandError, match="Recovery SSH is required for customized image builds"
    ):
        call_command(
            "imager",
            "gway-burn",
            "--base-image-uri",
            str(source),
            "--reserve-number",
            "7",
        )

    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.next_reservation")
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_validates_recovery_before_remote_reservation(
    mock_build,
    mock_next_reservation,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")

    with pytest.raises(
        CommandError, match="Recovery SSH is required for customized image builds"
    ):
        call_command(
            "imager",
            "gway-burn",
            "--base-image-uri",
            str(source),
        )

    mock_next_reservation.assert_not_called()
    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.next_reservation")
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_validates_base_before_remote_reservation(
    mock_build,
    mock_next_reservation,
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "missing.img"

    with pytest.raises(CommandError, match="Base image does not exist"):
        call_command(
            "imager",
            "gway-burn",
            "--base-image-uri",
            str(missing_source),
            "--output-dir",
            str(tmp_path / "out"),
            "--skip-recovery-ssh",
        )

    mock_next_reservation.assert_not_called()
    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager._ensure_image_minimum_size")
@patch("apps.imager.management.commands.imager.next_reservation")
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_preflights_expansion_before_remote_reservation(
    mock_build,
    mock_next_reservation,
    mock_ensure_minimum_size,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    mock_ensure_minimum_size.side_effect = ImagerBuildError("resize failed")

    with pytest.raises(CommandError, match="resize failed"):
        call_command(
            "imager",
            "gway-burn",
            "--base-image-uri",
            str(source),
            "--output-dir",
            str(tmp_path / "out"),
            "--skip-recovery-ssh",
        )

    resize_path = Path(mock_ensure_minimum_size.call_args.args[0])
    assert resize_path != source
    assert source.read_bytes() == b"pi"
    mock_next_reservation.assert_not_called()
    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.next_reservation")
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_passes_remote_claim_token(
    mock_build,
    mock_next_reservation,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    output_path = tmp_path / "gway-009.img"
    output_path.write_bytes(b"artifact")
    mock_next_reservation.return_value = SimpleNamespace(
        number=9,
        claim_token="claim-token",
    )
    mock_build.return_value = SimpleNamespace(
        name="gway-009",
        output_path=output_path,
        sha256="abc123",
        size_bytes=8,
        download_uri="",
        reservation={"hostname": "gway-009", "ipv4_address": "10.42.0.9"},
    )

    call_command(
        "imager",
        "gway-burn",
        "--base-image-uri",
        str(source),
        "--skip-recovery-ssh",
        "--minimum-image-size-gib",
        "0",
    )

    build_kwargs = mock_build.call_args.kwargs
    assert build_kwargs["reserve_number"] == 9
    assert build_kwargs["reservation_claim_token"] == "claim-token"


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.next_reservation")
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_gway_burn_fails_when_remote_reservation_fails(
    mock_build,
    mock_next_reservation,
    tmp_path: Path,
) -> None:
    source = tmp_path / "base.img"
    source.write_bytes(b"pi")
    mock_next_reservation.side_effect = RemoteReservationError("upstream unavailable")

    with pytest.raises(CommandError, match="upstream unavailable"):
        call_command(
            "imager",
            "gway-burn",
            "--base-image-uri",
            str(source),
            "--skip-recovery-ssh",
            "--minimum-image-size-gib",
            "0",
        )

    mock_build.assert_not_called()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_can_disable_reservation_env_default(
    mock_build,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """An explicit --no-reserve must override the instance default."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    monkeypatch.setenv("IMAGER_RESERVE_DEFAULT", "1")
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
            "reservation": None,
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "unreserved",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--no-reserve",
    )

    assert mock_build.call_args.kwargs["reserve_node"] is False


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_can_enable_connect_bootstrap(
    mock_build,
    tmp_path: Path,
) -> None:
    """The CLI opt-in should flow into image customization."""

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
            "reservation": None,
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "connect-enabled",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--enable-connect-bootstrap",
    )

    assert mock_build.call_args.kwargs["connect_bootstrap_enabled"] is True


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_passes_connect_auth_key_file(
    mock_build,
    tmp_path: Path,
) -> None:
    """The CLI should pass the private auth TOML path without reading or printing it."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    auth_profile = tmp_path / "rpi-connect-auth.toml"
    auth_profile.write_text('[rpi_connect]\nauth_key = "SECRET"\n', encoding="utf-8")
    auth_profile.chmod(0o600)
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
            "reservation": None,
        },
    )()

    stdout = StringIO()
    call_command(
        "imager",
        "build",
        "--name",
        "connect-auth",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--connect-auth-config",
        str(auth_profile),
        stdout=stdout,
    )

    assert mock_build.call_args.kwargs["connect_auth_key_path"] == auth_profile
    assert "SECRET" not in stdout.getvalue()


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_enables_connect_for_reserved_field_nodes(
    mock_build,
    tmp_path: Path,
) -> None:
    """Reserved GWAY images retain a remote recovery path by default."""

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
            "reservation": None,
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "gway-004",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--reserve",
        "--reserve-number",
        "4",
        "--reserve-prefix",
        "gway",
    )

    assert mock_build.call_args.kwargs["connect_bootstrap_enabled"] is True


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_can_skip_default_connect_for_reserved_field_nodes(
    mock_build,
    tmp_path: Path,
) -> None:
    """Connect remains explicitly suppressible for exceptional field images."""

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
            "reservation": None,
        },
    )()

    call_command(
        "imager",
        "build",
        "--name",
        "gway-004-no-connect",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--reserve",
        "--reserve-number",
        "4",
        "--skip-connect-bootstrap",
    )

    assert mock_build.call_args.kwargs["connect_bootstrap_enabled"] is False


@pytest.mark.django_db
def test_imager_build_command_rejects_nonpositive_reserve_number(
    tmp_path: Path,
) -> None:
    """Reservation suffixes are node numbers, so zero and negatives are invalid."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(
        CommandError, match="--reserve-number must be greater than zero"
    ):
        call_command(
            "imager",
            "build",
            "--name",
            "reserved",
            "--base-image-uri",
            str(output_path),
            "--skip-recovery-ssh",
            "--reserve",
            "--reserve-number",
            "0",
        )


@pytest.mark.django_db
def test_imager_build_command_rejects_invalid_storage_options_json(
    tmp_path: Path,
) -> None:
    """Regression: --storage-options must be valid JSON."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(CommandError, match="--storage-options must be valid JSON."):
        call_command(
            "imager",
            "build",
            "--name",
            "bad-storage-options-json",
            "--base-image-uri",
            str(output_path),
            "--skip-recovery-ssh",
            "--storage-options",
            "{invalid",
        )


@pytest.mark.django_db
def test_imager_build_command_rejects_non_object_storage_options_json(
    tmp_path: Path,
) -> None:
    """Regression: --storage-options must decode to a JSON object."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(
        CommandError, match="--storage-options must decode to a JSON object."
    ):
        call_command(
            "imager",
            "build",
            "--name",
            "bad-storage-options-type",
            "--base-image-uri",
            str(output_path),
            "--skip-recovery-ssh",
            "--storage-options",
            "[1,2,3]",
        )


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_passes_storage_options_to_backend(
    mock_build, tmp_path: Path
) -> None:
    """Regression: build command should pass parsed storage configuration to the backend."""

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
        "good-storage-options",
        "--base-image-uri",
        str(output_path),
        "--skip-recovery-ssh",
        "--storage-backend",
        "s3",
        "--storage-options",
        '{"bucket":"artifacts","access_key":"key"}',
    )

    assert mock_build.call_args.kwargs["storage_backend"] == "s3"
    assert mock_build.call_args.kwargs["storage_options"] == {
        "bucket": "artifacts",
        "access_key": "key",
    }


def test_sanitize_storage_options_masks_nested_secret_values() -> None:
    """Regression: persisted artifact metadata must not leak nested credentials."""

    assert _sanitize_storage_options(
        {
            "bucket": "artifacts",
            "credentials": {
                "secret": "cleartext-secret",
                "profile": {
                    "access_key": "cleartext-access-key",
                    "private_key": "cleartext-private-key",
                    "private_key_id": "cleartext-private-key-id",
                    "region": "us-east-1",
                },
                "azure": {
                    "account_key": "cleartext-account-key",
                    "shared_key": "cleartext-shared-key",
                },
            },
            "mirrors": [
                {"token": "cleartext-token", "endpoint": "https://example.invalid"},
                {"name": "public"},
            ],
        }
    ) == {
        "bucket": "artifacts",
        "credentials": {
            "secret": "***",
            "profile": {
                "access_key": "***",
                "private_key": "***",
                "private_key_id": "***",
                "region": "us-east-1",
            },
            "azure": {
                "account_key": "***",
                "shared_key": "***",
            },
        },
        "mirrors": [
            {"token": "***", "endpoint": "https://example.invalid"},
            {"name": "public"},
        ],
    }


@pytest.mark.django_db
@patch("apps.imager.management.commands.imager.build_rpi4b_image")
def test_imager_build_command_reads_recovery_authorized_key_files(
    mock_build, tmp_path: Path
) -> None:
    """Regression: recovery key files should flow into build customization args."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    authorized_key_file = tmp_path / "recovery.pub"
    authorized_key_file.write_text(
        f"# comment\n{VALID_RECOVERY_KEY_ONE}\n\n{VALID_RECOVERY_KEY_TWO}\n",
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
def test_imager_build_command_ignores_non_public_key_lines(
    mock_build, tmp_path: Path
) -> None:
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
def test_imager_build_command_reads_inline_recovery_authorized_keys(
    mock_build, tmp_path: Path
) -> None:
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


def test_imager_build_command_reports_non_utf8_recovery_key_file(
    tmp_path: Path,
) -> None:
    """Regression: non-UTF8 key files should return a clean command error."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")
    authorized_key_file = tmp_path / "recovery.pub"
    authorized_key_file.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(
        CommandError, match="Could not read recovery authorized key file"
    ):
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


def test_imager_build_command_requires_recovery_ssh_key_by_default(
    tmp_path: Path,
) -> None:
    """Regression: customized builds should fail fast unless recovery SSH is explicit."""

    output_path = tmp_path / "artifact.img"
    output_path.write_bytes(b"pi")

    with pytest.raises(
        CommandError, match="Recovery SSH is required for customized image builds"
    ):
        call_command(
            "imager",
            "build",
            "--name",
            "recovery-required",
            "--base-image-uri",
            str(output_path),
        )


def test_imager_build_command_rejects_skip_recovery_ssh_with_keys(
    tmp_path: Path,
) -> None:
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
def test_imager_build_command_allows_explicit_skip_recovery_ssh(
    mock_build, tmp_path: Path
) -> None:
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
    assert mock_build.call_args.kwargs["skip_recovery_ssh"] is True
    assert "recovery_ssh=disabled (--skip-recovery-ssh)" in stdout.getvalue()


def test_customize_image_writes_recovery_ssh_files_when_authorized_keys_provided(
    tmp_path: Path,
) -> None:
    """Regression: recovery customization must enable first-boot SSH access files."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"pi")
    written_files: dict[str, tuple[str, str | None]] = {}
    guestfish_batches: list[list[str]] = []
    boot_partition_scripts: list[str] = []

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
                uploaded_bytes = Path(parts[1]).read_bytes()
                assert b"\r" not in uploaded_bytes
                written_files[parts[2]] = (uploaded_bytes.decode("utf-8"), None)
            elif parts and parts[0] == "chmod":
                content, _mode = written_files[parts[2]]
                written_files[parts[2]] = (content, parts[1])

    def capture_boot_partition_script(
        image_path_arg: Path,
        script: str,
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert "boot partition" in error_message
        boot_partition_scripts.append(script)
        assert script.startswith("run\nmount /dev/sda1 /\n")
        assert script.endswith("umount /\n")
        for command in script.splitlines():
            parts = shlex.split(command)
            if parts and parts[0] == "upload":
                written_files[parts[2]] = (
                    Path(parts[1]).read_text(encoding="utf-8"),
                    None,
                )

    recovery_access = type(
        "RecoverySSHAccess",
        (),
        {
            "username": "fieldops",
            "authorized_keys": (
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ),
            "enabled": True,
        },
    )()

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch(
            "apps.imager.services._guestfish_run_commands",
            side_effect=capture_guestfish,
        ),
        patch(
            "apps.imager.services._run_guestfish_raw_script",
            side_effect=capture_boot_partition_script,
        ),
        patch(
            "apps.imager.services.build_engine._generate_recovery_userconf_password_hash",
            return_value="$6$test-hash",
        ),
    ):
        _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            recovery_ssh_access=recovery_access,
        )

    assert len(guestfish_batches) == 2
    assert len(boot_partition_scripts) == 3
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
    assert "/boot/firmware/userconf.txt" not in written_files
    assert "/boot/firmware/ssh" not in written_files
    assert "/boot/firstrun.sh" not in written_files
    assert "/userconf.txt" in written_files
    assert "/ssh" in written_files
    assert "/firstrun.sh" in written_files

    bootstrap_script, bootstrap_mode = written_files[
        "/usr/local/bin/arthexis-bootstrap.sh"
    ]
    recovery_script, recovery_mode = written_files[
        "/usr/local/bin/arthexis-recovery-access.sh"
    ]
    recovery_keys, keys_mode = written_files[
        "/usr/local/share/arthexis/recovery_authorized_keys"
    ]
    recovery_service, recovery_service_mode = written_files[
        "/etc/systemd/system/arthexis-recovery-access.service"
    ]
    firstrun_script, _firstrun_mode = written_files["/firstrun.sh"]
    ssh_marker, ssh_marker_mode = written_files["/ssh"]
    sshd_config, sshd_mode = written_files[
        "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf"
    ]
    userconf, userconf_mode = written_files["/userconf.txt"]

    assert bootstrap_mode == "0755"
    assert "required_packages+=(git ca-certificates)" in bootstrap_script
    assert "python3-dev" in bootstrap_script
    assert "build-essential" in bootstrap_script
    assert "add_required_package_if_missing redis-server" in bootstrap_script
    assert (
        'connect_bootstrap_enabled="${ARTHEXIS_ENABLE_CONNECT_BOOTSTRAP:-0}"'
        in bootstrap_script
    )
    assert (
        "for package in rpi-connect wayvnc wfplug-connect lightdm pi-greeter wayfire labwc"
        in bootstrap_script
    )
    assert "rpd-wayland-core" not in bootstrap_script
    assert 'if [ "$connect_bootstrap_enabled" = "1" ]; then' in bootstrap_script
    assert 'optional_connect_packages+=("$package")' in bootstrap_script
    assert 'for package in "${optional_connect_packages[@]}"; do' in bootstrap_script
    assert "continuing bootstrap" in bootstrap_script
    assert "rpi-connect-lite" not in bootstrap_script
    assert (
        'if [ "$connect_bootstrap_enabled" = "1" ] && id "$CONNECT_SCREEN_SHARE_USER" >/dev/null 2>&1; then'
        in bootstrap_script
    )
    assert "systemctl disable userconfig.service" in bootstrap_script
    assert "autologin-user=$CONNECT_SCREEN_SHARE_USER" in bootstrap_script
    assert "autologin-session=rpd-labwc" in bootstrap_script
    assert 'sudo -u "$CONNECT_SCREEN_SHARE_USER" env' in bootstrap_script
    assert (
        "CONNECT_AUTH_KEY=/usr/local/share/arthexis/rpi-connect-auth.key"
        in bootstrap_script
    )
    assert 'rpi-connect signin -auth-key "$auth_key"' in bootstrap_script
    assert 'shred -u "$CONNECT_AUTH_KEY"' in bootstrap_script
    assert "cleanup_connect_auth_key()" in bootstrap_script
    assert "trap cleanup_connect_auth_key EXIT" not in bootstrap_script
    assert "for signin_attempt in 1 2 3; do" in bootstrap_script
    assert 'if [ "$signin_attempt" -lt 3 ]; then' in bootstrap_script
    assert "sign-in failed after retries; continuing bootstrap" in bootstrap_script
    assert "rpi-connect vnc on" in bootstrap_script
    assert (
        'if ! command -v git >/dev/null 2>&1 && [ ! -f "$ARTHEXIS_BUNDLE" ]; then'
        in bootstrap_script
    )
    assert (
        "chmod +x ./install.sh ./env-refresh.sh ./start.sh ./manage.py ./command.sh"
        in bootstrap_script
    )
    assert (
        'chmod +x "$APP_HOME"/install.sh "$APP_HOME"/env-refresh.sh "$APP_HOME"/start.sh "$APP_HOME"/manage.py "$APP_HOME"/command.sh'
        in bootstrap_script
    )
    assert bootstrap_script.index(
        'tar -xzf "$ARTHEXIS_BUNDLE"'
    ) < bootstrap_script.index('chmod +x "$APP_HOME"/install.sh')
    assert bootstrap_script.index(
        'chmod +x "$APP_HOME"/install.sh'
    ) < bootstrap_script.index('if [ ! -x "$APP_HOME/start.sh" ]; then')
    assert (
        "No bundled Arthexis suite was available and ARTHEXIS_GIT_URL is not configured."
        in bootstrap_script
    )
    assert "pass an authenticated --git-url" in bootstrap_script
    assert bootstrap_script.index(
        "ARTHEXIS_GIT_URL is not configured"
    ) < bootstrap_script.index('git clone --depth 1 "${ARTHEXIS_GIT_URL}" "$APP_HOME"')
    assert "cat >/usr/local/bin/arthexis <<'EOF'" in bootstrap_script
    assert 'export ARTHEXIS_CALLER_CWD="$(pwd -P)"' in bootstrap_script
    assert 'exec "$APP_HOME/command.sh" "$@"' in bootstrap_script
    assert "libpango-1.0-0 libpangoft2-1.0-0 libcairo2" in bootstrap_script
    assert "remove_app_env_value ARTHEXIS_RUNSERVER_HOST 0.0.0.0" in bootstrap_script
    assert "set_app_env_default()" in bootstrap_script
    assert "set_app_env_value()" in bootstrap_script
    assert "bootstrap_normalize_runtime_role()" in bootstrap_script
    assert "bootstrap_persist_runtime_role()" in bootstrap_script
    assert "bootstrap_role_env_defaults()" in bootstrap_script
    assert "NODE_ROLE=Terminal" in bootstrap_script
    assert "watchtower|constellation) NODE_ROLE=Watchtower" in bootstrap_script
    assert "export NODE_ROLE" in bootstrap_script
    assert 'set_app_env_value NODE_ROLE "$NODE_ROLE"' in bootstrap_script
    assert bootstrap_script.index(
        "bootstrap_persist_runtime_role\nbootstrap_role_env_defaults"
    ) < bootstrap_script.index("mapfile -t install_args < <(bootstrap_install_args)")
    assert bootstrap_script.index(
        "bootstrap_normalize_runtime_role\n\nrequired_packages=()"
    ) < bootstrap_script.index('case "${NODE_ROLE:-Terminal}" in')
    assert "set_app_env_default OCPP_AUTHORIZATION_POLICY open" in bootstrap_script
    assert (
        "set_app_env_default ARTHEXIS_RUNSERVER_HOST 127.0.0.1" not in bootstrap_script
    )
    assert "set_app_env_default ARTHEXIS_RUNSERVER_HOST 0.0.0.0" not in bootstrap_script
    assert 'chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"' in bootstrap_script
    assert "mapfile -t install_args < <(bootstrap_install_args)" in bootstrap_script
    assert './install.sh "${install_args[@]}"' in bootstrap_script
    assert (
        "printf '%s\\n' --satellite --no-rfid-service --systemd \"$start_arg\" --repair"
        in bootstrap_script
    )
    assert (
        "printf '%s\\n' --terminal --no-celery --systemd \"$start_arg\" --repair"
        in bootstrap_script
    )
    assert (
        "./install.sh --terminal --no-celery --systemd --start --repair"
        not in bootstrap_script
    )
    assert "bootstrap_select_recovery_ap_iface()" in bootstrap_script
    assert "bootstrap_validate_recovery_ap_psk()" in bootstrap_script
    assert "bootstrap_recovery_ap_psk()" in bootstrap_script
    assert "local ap_psk" in bootstrap_script
    assert 'ap_psk="$(bootstrap_recovery_ap_psk)" || return 0' in bootstrap_script
    assert "ARTHEXIS_RECOVERY_AP_PSK_FILE" in bootstrap_script
    assert "openssl rand -base64 24" in bootstrap_script
    assert 'if [ -s "$psk_file" ]; then' in bootstrap_script
    assert (
        'bootstrap_validate_recovery_ap_psk "$ARTHEXIS_RECOVERY_AP_PSK" || return 1'
        in bootstrap_script
    )
    assert (
        'bootstrap_validate_recovery_ap_psk "$file_psk" || return 1' in bootstrap_script
    )
    assert (
        'bootstrap_validate_recovery_ap_psk "$generated_psk" || return 1'
        in bootstrap_script
    )
    assert "LC_ALL=C tr -dc 'A-Za-z0-9'" in bootstrap_script
    assert (
        'install -d -m 700 "$(dirname "$psk_file")" >/dev/null 2>&1 || return 1'
        in bootstrap_script
    )
    assert (
        '( umask 077 && printf \'%s\\n\' "$generated_psk" > "$psk_file" ) || return 1'
        in bootstrap_script
    )
    assert 'local ap_psk="${ARTHEXIS_RECOVERY_AP_PSK:-}"' not in bootstrap_script
    assert "arthexis${number}" not in bootstrap_script
    assert 'nmcli con delete "$ap_ssid"' in bootstrap_script
    assert 'connection.interface-name "$ap_iface"' in bootstrap_script
    assert "connection.autoconnect-priority 100" in bootstrap_script
    assert "802-11-wireless.hidden yes" in bootstrap_script
    assert 'wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$ap_psk"' in bootstrap_script
    assert "ipv4.addresses 10.42.0.1/16" in bootstrap_script
    assert "ipv4.addresses 10.42.0.1/24" not in bootstrap_script
    assert 'if ! nmcli con mod "$ap_ssid"' in bootstrap_script
    assert (
        'wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$ap_psk" >/dev/null 2>&1; then'
        in bootstrap_script
    )
    assert (
        'nmcli con delete "$ap_ssid" >/dev/null 2>&1 || true\n    return 0\n  fi\n  nmcli con up'
        in bootstrap_script
    )
    assert (
        'wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$ap_psk" >/dev/null 2>&1 || true'
        not in bootstrap_script
    )
    assert "./env-refresh.sh --deps-only" not in bootstrap_script
    assert "bootstrap_start_app()" in bootstrap_script
    assert 'sudo -u "$APP_USER" ./start.sh' in bootstrap_script
    assert bootstrap_script.index(
        ".venv/bin/python manage.py migrate --check"
    ) < bootstrap_script.index("\nbootstrap_start_app\n")
    assert (
        'for candidate in "${ARTHEXIS_BOOTSTRAP_USER:-}" fieldops arthe "${SUDO_USER:-}"; do'
        in bootstrap_script
    )
    apt_update_retry = "apt_get_update_with_clock_retry"
    assert apt_update_retry in bootstrap_script
    assert "ARTHEXIS_BOOTSTRAP_APT_UPDATE_ATTEMPTS" in bootstrap_script
    assert "apt release metadata is not valid yet" in bootstrap_script
    assert "timedatectl set-ntp true" in bootstrap_script
    assert "add_required_package_if_missing curl" in bootstrap_script
    assert "register_downstream_with_arthexis()" in bootstrap_script
    assert "node register-curl" in bootstrap_script
    assert "ARTHEXIS_DOWNSTREAM_REGISTRATION_BASE_URL" in bootstrap_script
    assert (
        "ARTHEXIS_LOCAL_REGISTRATION_BASE_URL:-http://localhost:${ARTHEXIS_RUNSERVER_PORT:-8888}"
        in bootstrap_script
    )
    assert "https://localhost:${ARTHEXIS_RUNSERVER_PORT:-8888}" not in bootstrap_script
    assert "BOOTSTRAP_COMPLETE=/var/lib/arthexis/bootstrap-complete" in bootstrap_script
    assert "disable_bootstrap_service()" in bootstrap_script
    assert 'if [ -f "$BOOTSTRAP_COMPLETE" ]; then' in bootstrap_script
    assert "systemctl disable arthexis-bootstrap.service" in bootstrap_script
    assert (
        "rm -f /etc/systemd/system/multi-user.target.wants/arthexis-bootstrap.service"
        in bootstrap_script
    )
    assert 'touch "$BOOTSTRAP_COMPLETE"' in bootstrap_script
    assert bootstrap_script.index(
        './install.sh "${install_args[@]}"'
    ) < bootstrap_script.index('touch "$BOOTSTRAP_COMPLETE"')
    assert (
        "timedatectl show -p NTPSynchronized 2>/dev/null | grep -q" in bootstrap_script
    )
    assert "timedatectl show -p NTPSynchronized --value" not in bootstrap_script
    assert "systemd-timesyncd.service" in bootstrap_script
    assert "if apt_get_update_with_clock_retry; then" in bootstrap_script
    assert "else\n      status=$?\n    fi" in bootstrap_script
    assert "fi\n    status=$?" not in bootstrap_script
    assert 'grep -qi "not valid yet" <<<"$output"' in bootstrap_script
    assert (
        'printf \'%s\\n\' "$output" | grep -qi "not valid yet"' not in bootstrap_script
    )
    assert "apt-get update || { sleep 10; apt-get update; }" not in bootstrap_script
    assert "apt-get install -y --no-install-recommends" in bootstrap_script
    assert bootstrap_script.index(apt_update_retry) < bootstrap_script.index(
        "apt-get install"
    )
    assert bootstrap_script.index("apt-get install") < bootstrap_script.index(
        "git clone"
    )
    assert recovery_mode == "0755"
    assert keys_mode == "0600"
    assert ssh_marker == ""
    assert ssh_marker_mode is None
    assert sshd_mode == "0644"
    assert recovery_service_mode == "0644"
    assert userconf.startswith("fieldops:$6$")
    assert userconf.endswith("\n")
    assert userconf_mode is None
    assert "RECOVERY_USER=fieldops" in recovery_script
    assert "NOPASSWD:ALL" in recovery_script
    assert "while IFS= read -r recovery_key" in recovery_script
    assert 'grep -qxF "$recovery_key"' in recovery_script
    assert (
        'printf \'%s\\n\' "$recovery_key" >> "$RECOVERY_HOME/.ssh/authorized_keys"'
        in recovery_script
    )
    assert (
        'install -m 600 -o "$RECOVERY_USER" -g "$RECOVERY_USER" '
        "/usr/local/share/arthexis/recovery_authorized_keys "
        '"$RECOVERY_HOME/.ssh/authorized_keys"' not in recovery_script
    )
    assert "systemctl enable ssh" in recovery_script
    assert "systemctl restart ssh" not in recovery_script
    assert (
        "Before=ssh.service sshd.service arthexis-bootstrap.service" in recovery_service
    )
    assert "ExecStart=/usr/local/bin/arthexis-recovery-access.sh" in recovery_service
    assert (
        recovery_keys == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery\n"
    )
    assert "/usr/local/bin/arthexis-recovery-access.sh" in firstrun_script
    assert (
        "arthexis-recovery-access.sh failed; continuing with bootstrap"
        in firstrun_script
    )
    assert "PasswordAuthentication no" in sshd_config


def test_customize_image_does_not_add_recovery_boot_hook_when_recovery_is_disabled(
    tmp_path: Path,
) -> None:
    """Regression: first-boot recovery hook should be gated by explicit recovery settings."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"pi")
    written_files: dict[str, tuple[str, str | None]] = {}
    guestfish_batches: list[list[str]] = []
    boot_partition_scripts: list[str] = []

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
                written_files[parts[2]] = (
                    Path(parts[1]).read_text(encoding="utf-8"),
                    None,
                )
            elif parts and parts[0] == "chmod":
                content, _mode = written_files[parts[2]]
                written_files[parts[2]] = (content, parts[1])

    def capture_boot_partition_script(
        image_path_arg: Path,
        script: str,
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert "boot partition" in error_message
        boot_partition_scripts.append(script)
        assert script.startswith("run\nmount /dev/sda1 /\n")
        assert script.endswith("umount /\n")
        for command in script.splitlines():
            parts = shlex.split(command)
            if parts and parts[0] == "upload":
                written_files[parts[2]] = (
                    Path(parts[1]).read_text(encoding="utf-8"),
                    None,
                )

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch(
            "apps.imager.services._guestfish_run_commands",
            side_effect=capture_guestfish,
        ),
        patch(
            "apps.imager.services._run_guestfish_raw_script",
            side_effect=capture_boot_partition_script,
        ),
    ):
        _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            recovery_ssh_access=None,
        )

    firstrun_script, _firstrun_mode = written_files["/firstrun.sh"]
    assert "/usr/local/bin/arthexis-recovery-access.sh" not in firstrun_script
    assert "/etc/systemd/system/arthexis-bootstrap.service" in written_files
    assert "/etc/systemd/system/arthexis-recovery-access.service" not in written_files
    assert len(guestfish_batches) == 2
    assert len(boot_partition_scripts) == 2
    assert "mkdir-p /etc/systemd/system/multi-user.target.wants" in guestfish_batches[0]
    assert (
        "ln-sf /etc/systemd/system/arthexis-bootstrap.service "
        "/etc/systemd/system/multi-user.target.wants/arthexis-bootstrap.service"
    ) in guestfish_batches[0]
    assert guestfish_batches[1] == [
        "rm-f /boot/firmware/userconf.txt",
        "rm-f /boot/userconf.txt",
        "rm-f /boot/firmware/ssh",
        "rm-f /boot/ssh",
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
    assert "rm-f /userconf.txt" in boot_partition_scripts[0]
    assert "rm-f /ssh" in boot_partition_scripts[0]


def test_render_bootstrap_script_can_enable_connect_default() -> None:
    """Rendered bootstrap can bake in the Raspberry Pi Connect opt-in."""

    default_script = _render_bootstrap_script()
    enabled_script = _render_bootstrap_script(connect_bootstrap_enabled=True)

    assert (
        'connect_bootstrap_enabled="${ARTHEXIS_ENABLE_CONNECT_BOOTSTRAP:-0}"'
        in default_script
    )
    assert (
        'connect_bootstrap_enabled="${ARTHEXIS_ENABLE_CONNECT_BOOTSTRAP:-1}"'
        in enabled_script
    )


def test_render_bootstrap_script_retries_apt_when_clock_is_stale() -> None:
    """Regression: first boot should recover from apt metadata newer than Pi clock."""

    script = _render_bootstrap_script()

    assert "wait_for_bootstrap_clock_sync()" in script
    assert "apt_get_update_with_clock_retry()" in script
    assert "ARTHEXIS_BOOTSTRAP_APT_UPDATE_ATTEMPTS" in script
    assert 'grep -qi "not valid yet" <<<"$output"' in script
    assert "timedatectl set-ntp true" in script
    assert "timedatectl show -p NTPSynchronized 2>/dev/null | grep -q" in script
    assert "timedatectl show -p NTPSynchronized --value" not in script
    assert "systemctl restart systemd-timesyncd.service" in script
    assert "if apt_get_update_with_clock_retry; then" in script
    assert "else\n      status=$?\n    fi" in script
    assert "fi\n    status=$?" not in script
    assert (
        'if ! command -v git >/dev/null 2>&1 && [ ! -f "$ARTHEXIS_BUNDLE" ]; then'
        in script
    )
    assert script.index("ARTHEXIS_BUNDLE=") < script.index("required_packages=()")
    assert "apt-get update || { sleep 10; apt-get update; }" not in script


def test_render_bootstrap_script_uses_runtime_install_path() -> None:
    """First boot should not install CI-only deps or leave a foreground server."""

    script = _render_bootstrap_script()

    assert "add_required_package_if_missing()" in script
    assert "python3-venv" in script
    assert "python3-dev" in script
    assert "build-essential" in script
    assert "libpango-1.0-0" in script
    assert "libpangoft2-1.0-0" in script
    assert "libcairo2" in script
    assert "libgdk-pixbuf-2.0-0" in script
    assert "shared-mime-info" in script
    assert "fonts-dejavu-core" in script
    assert "$1 !~ /^#/" in script
    assert "printf '127.0.1.1\\t%s\\n' \"$NODE_HOSTNAME\" >> /etc/hosts" in script
    assert 'grep -q "ok installed"' in script
    assert 'grep -q "install ok installed"' not in script
    assert "bootstrap_app_user()" in script
    assert "bootstrap_install_args()" in script
    assert "bootstrap_select_recovery_ap_iface()" in script
    assert "bootstrap_validate_recovery_ap_psk()" in script
    assert "bootstrap_recovery_ap_psk()" in script
    assert "bootstrap_enable_recovery_ap()" in script
    assert "arthexis-${short_number}" in script
    assert "arthexis${number}" not in script
    assert 'ap_psk="$(bootstrap_recovery_ap_psk)" || return 0' in script
    assert "ARTHEXIS_RECOVERY_AP_PSK_FILE" in script
    assert 'if [ -s "$psk_file" ]; then' in script
    assert 'bootstrap_validate_recovery_ap_psk "$file_psk" || return 1' in script
    assert 'bootstrap_validate_recovery_ap_psk "$generated_psk" || return 1' in script
    assert "LC_ALL=C tr -dc 'A-Za-z0-9'" in script
    assert 'local ap_psk="${ARTHEXIS_RECOVERY_AP_PSK:-}"' not in script
    assert 'nmcli con delete "$ap_ssid"' in script
    assert 'connection.interface-name "$ap_iface"' in script
    assert "connection.autoconnect-priority 100" in script
    assert "802-11-wireless.hidden yes" in script
    assert "ipv4.addresses 10.42.0.1/16" in script
    assert "ipv4.addresses 10.42.0.1/24" not in script
    assert 'if ! nmcli con mod "$ap_ssid"' in script
    assert (
        'wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$ap_psk" >/dev/null 2>&1; then'
        in script
    )
    assert (
        'wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$ap_psk" >/dev/null 2>&1 || true'
        not in script
    )
    assert "remove_app_env_value ARTHEXIS_RUNSERVER_HOST 0.0.0.0" in script
    assert "set_app_env_default()" in script
    assert "set_app_env_value()" in script
    assert "bootstrap_normalize_runtime_role()" in script
    assert "bootstrap_persist_runtime_role()" in script
    assert "bootstrap_role_env_defaults()" in script
    assert "NODE_ROLE=Terminal" in script
    assert "watchtower|constellation) NODE_ROLE=Watchtower" in script
    assert "export NODE_ROLE" in script
    assert 'set_app_env_value NODE_ROLE "$NODE_ROLE"' in script
    assert script.index(
        "bootstrap_persist_runtime_role\nbootstrap_role_env_defaults"
    ) < script.index("mapfile -t install_args < <(bootstrap_install_args)")
    assert script.index(
        "bootstrap_normalize_runtime_role\n\nrequired_packages=()"
    ) < script.index('case "${NODE_ROLE:-Terminal}" in')
    assert "set_app_env_default OCPP_AUTHORIZATION_POLICY open" in script
    assert "set_app_env_default ARTHEXIS_RUNSERVER_HOST 127.0.0.1" not in script
    assert "set_app_env_default ARTHEXIS_RUNSERVER_HOST 0.0.0.0" not in script
    assert 'chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"' in script
    assert "wait_for_bootstrap_clock_sync || true" in script
    assert "mapfile -t install_args < <(bootstrap_install_args)" in script
    assert './install.sh "${install_args[@]}"' in script
    assert (
        "printf '%s\\n' --satellite --no-rfid-service --systemd \"$start_arg\" --repair"
        in script
    )
    assert (
        "printf '%s\\n' --terminal --no-celery --systemd \"$start_arg\" --repair"
        in script
    )
    assert (
        "./install.sh --terminal --no-celery --systemd --start --repair" not in script
    )
    assert "BOOTSTRAP_COMPLETE=/var/lib/arthexis/bootstrap-complete" in script
    assert "disable_bootstrap_service()" in script
    assert 'if [ -f "$BOOTSTRAP_COMPLETE" ]; then' in script
    assert "systemctl disable arthexis-bootstrap.service" in script
    assert (
        "rm -f /etc/systemd/system/multi-user.target.wants/arthexis-bootstrap.service"
        in script
    )
    assert 'touch "$BOOTSTRAP_COMPLETE"' in script
    assert script.index('./install.sh "${install_args[@]}"') < script.index(
        'touch "$BOOTSTRAP_COMPLETE"'
    )
    assert "./env-refresh.sh --deps-only" not in script
    assert "ARTHEXIS_INCLUDE_QA_REQUIREMENTS=1" not in script
    assert "bootstrap_start_app()" in script
    assert 'sudo -u "$APP_USER" ./start.sh' in script
    assert script.index(".venv/bin/python manage.py migrate --check") < script.index(
        "\nbootstrap_start_app\n"
    )


def test_render_bootstrap_script_rejects_invalid_runtime_recovery_ap_psks(
    tmp_path: Path,
) -> None:
    """Runtime env/file PSKs should fail before NetworkManager sees them."""

    script = _render_bootstrap_script()
    start = script.index("bootstrap_validate_recovery_ap_psk()")
    end = script.index("\nbootstrap_enable_recovery_ap()", start)
    recovery_psk_functions = script[start:end]
    psk_file = tmp_path / "recovery-ap.psk"
    psk_file.write_text("short\n", encoding="utf-8")
    runner = tmp_path / "check-recovery-ap-psk.sh"
    runner.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                recovery_psk_functions,
                "if ARTHEXIS_RECOVERY_AP_PSK=short bootstrap_recovery_ap_psk; then exit 11; fi",
                "ARTHEXIS_RECOVERY_AP_PSK=valid-psk bootstrap_recovery_ap_psk | grep -qx valid-psk",
                (
                    "if ARTHEXIS_RECOVERY_AP_PSK= "
                    f"ARTHEXIS_RECOVERY_AP_PSK_FILE={shlex.quote(str(psk_file))} "
                    "bootstrap_recovery_ap_psk; then exit 12; fi"
                ),
                f"printf '%s\\n' valid-file > {shlex.quote(str(psk_file))}",
                (
                    "ARTHEXIS_RECOVERY_AP_PSK= "
                    f"ARTHEXIS_RECOVERY_AP_PSK_FILE={shlex.quote(str(psk_file))} "
                    "bootstrap_recovery_ap_psk | grep -qx valid-file"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    subprocess.run(["bash", str(runner)], check=True)


def test_customize_image_writes_reserved_node_metadata(tmp_path: Path) -> None:
    """Reserved images should carry the planned hostname into first boot."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"pi")
    reservation = ImageReservation(
        hostname="gway-004",
        hostname_prefix="gway",
        number=4,
        ipv4_address="10.42.0.4",
        network_cidr="10.42.0.0/16",
        parent_hostname="gway-001",
        role_name="Satellite",
        claim_token="claim-token",
    )
    written_files: dict[str, tuple[str, str | None]] = {}
    guestfish_batches: list[list[str]] = []
    boot_partition_scripts: list[str] = []

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
                written_files[parts[2]] = (
                    Path(parts[1]).read_text(encoding="utf-8"),
                    None,
                )
            elif parts and parts[0] == "chmod":
                content, _mode = written_files[parts[2]]
                written_files[parts[2]] = (content, parts[1])

    def capture_boot_partition_script(
        image_path_arg: Path,
        script: str,
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert "boot partition" in error_message
        boot_partition_scripts.append(script)
        assert script.startswith("run\nmount /dev/sda1 /\n")
        assert script.endswith("umount /\n")

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch(
            "apps.imager.services._guestfish_run_commands",
            side_effect=capture_guestfish,
        ),
        patch(
            "apps.imager.services._run_guestfish_raw_script",
            side_effect=capture_boot_partition_script,
        ),
    ):
        _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            reservation=reservation,
        )

    assert "/usr/local/share/arthexis/reserved-node.env" in written_files
    assert "/usr/local/share/arthexis/reserved-node.json" in written_files
    env_payload, env_mode = written_files["/usr/local/share/arthexis/reserved-node.env"]
    json_payload, json_mode = written_files[
        "/usr/local/share/arthexis/reserved-node.json"
    ]
    bootstrap_script, _bootstrap_mode = written_files[
        "/usr/local/bin/arthexis-bootstrap.sh"
    ]
    assert env_mode == "0600"
    assert json_mode == "0644"
    assert "NODE_HOSTNAME=gway-004" in env_payload
    assert "NODE_ROLE=Satellite" in env_payload
    assert "NODE_RESERVED_CLAIM_TOKEN=claim-token" in env_payload
    reservation_json = json.loads(json_payload)
    assert reservation_json["hostname"] == "gway-004"
    assert "claim_token" not in reservation_json
    assert reservation_json["claim_token_baked"] is True
    assert "hostnamectl set-hostname" in bootstrap_script
    assert any(
        "upload" in command and "/usr/local/share/arthexis/reserved-node.env" in command
        for command in guestfish_batches[1]
    )
    assert any(
        "upload" in script and "/firstrun.sh" in script
        for script in boot_partition_scripts
    )


def test_customize_image_provisions_recovery_ap_psk(tmp_path: Path) -> None:
    """Regression: recovery AP credentials are baked into images before first boot."""

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
                mode = None
                written_files[parts[2]] = (
                    Path(parts[1]).read_text(encoding="utf-8"),
                    mode,
                )
            if parts and parts[0] == "chmod":
                payload, _previous_mode = written_files[parts[2]]
                written_files[parts[2]] = (payload, parts[1])

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch(
            "apps.imager.services._guestfish_run_commands",
            side_effect=capture_guestfish,
        ),
        patch("apps.imager.services._run_guestfish_raw_script"),
    ):
        result = _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            recovery_ap_psk="known-recovery-passphrase",
        )

    assert result.recovery_ap_psk_path == "/etc/arthexis/recovery-ap.psk"
    assert written_files["/etc/arthexis/recovery-ap.psk"] == (
        "known-recovery-passphrase\n",
        "0600",
    )
    flattened_commands = "\n".join(
        command for batch in guestfish_batches for command in batch
    )
    assert "mkdir-p /etc/arthexis" in flattened_commands


def test_customize_image_writes_suite_bundle_and_selected_network_profiles(
    tmp_path: Path,
) -> None:
    """Regression: customized images can boot from bundled source and copied Wi-Fi profiles."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"pi")
    suite_source = make_suite_source(tmp_path)
    network_dir = tmp_path / "networks"
    network_dir.mkdir()
    network_profile = network_dir / "home.nmconnection"
    network_profile.write_text(
        "[connection]\nid=Home WiFi\n\n[wifi-security]\npsk=secret\n",
        encoding="utf-8",
    )
    selected_profiles = select_host_network_profiles(
        profile_dir=network_dir,
        names=("Home WiFi",),
    )
    guestfish_batches: list[list[str]] = []
    boot_partition_scripts: list[str] = []

    def capture_guestfish(
        image_path_arg: Path,
        commands: list[str],
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert error_message
        guestfish_batches.append(commands)

    def capture_boot_partition_script(
        image_path_arg: Path,
        script: str,
        *,
        error_message: str,
    ) -> None:
        assert image_path_arg == image_path
        assert "boot partition" in error_message
        boot_partition_scripts.append(script)
        assert script.startswith("run\nmount /dev/sda1 /\n")
        assert script.endswith("umount /\n")

    with (
        patch("apps.imager.services._ensure_guestfish"),
        patch(
            "apps.imager.services._guestfish_run_commands",
            side_effect=capture_guestfish,
        ),
        patch(
            "apps.imager.services._run_guestfish_raw_script",
            side_effect=capture_boot_partition_script,
        ),
    ):
        result = _customize_image(
            image_path,
            git_url="https://github.com/arthexis/arthexis.git",
            recovery_ssh_access=None,
            suite_source_path=suite_source,
            network_profiles=selected_profiles,
        )

    flattened_commands = "\n".join(
        command for batch in guestfish_batches for command in batch
    )
    assert result.suite_bundle is not None
    assert result.suite_bundle.file_count == 5
    assert result.network_profiles[0].name == "Home WiFi"
    assert (
        f"upload {shlex.quote(str(network_profile))} /etc/NetworkManager/system-connections/home.nmconnection"
        in flattened_commands
    )
    assert (
        "chmod 0600 /etc/NetworkManager/system-connections/home.nmconnection"
        in flattened_commands
    )
    assert "upload" in flattened_commands
    assert "/usr/local/share/arthexis/arthexis-suite.tar.gz" in flattened_commands
    assert any(
        "upload" in script and "/firstrun.sh" in script
        for script in boot_partition_scripts
    )


def test_select_host_network_profiles_skips_symlinked_profiles(tmp_path: Path) -> None:
    """Regression: host network copying should not follow symlinks out of the profile directory."""

    network_dir = tmp_path / "networks"
    network_dir.mkdir()
    real_profile = network_dir / "home.nmconnection"
    real_profile.write_text("[connection]\nid=Home WiFi\n", encoding="utf-8")
    outside_profile = tmp_path / "outside.nmconnection"
    outside_profile.write_text("[connection]\nid=Outside WiFi\n", encoding="utf-8")
    try:
        (network_dir / "outside-link.nmconnection").symlink_to(outside_profile)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable on this host: {exc}")

    selected_profiles = select_host_network_profiles(
        profile_dir=network_dir,
        copy_all=True,
    )

    assert [profile.name for profile in selected_profiles] == ["Home WiFi"]


def test_build_rpi4b_image_rejects_invalid_recovery_ssh_username(
    tmp_path: Path,
) -> None:
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


def test_build_rpi4b_image_rejects_recovery_ssh_when_customize_is_disabled(
    tmp_path: Path,
) -> None:
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


def test_build_rpi4b_image_rejects_reservation_when_customize_is_disabled(
    tmp_path: Path,
) -> None:
    """Regression: reserved node images need injected metadata to be claimable."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(
        ImagerBuildError, match="Reserved node images require image customization"
    ):
        build_rpi4b_image(
            name="reserved-no-customize",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=False,
            reserve_node=True,
        )


def test_build_rpi4b_image_rejects_recovery_username_without_keys(
    tmp_path: Path,
) -> None:
    """Regression: explicit recovery usernames without keys should fail fast."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(
        ImagerBuildError,
        match="Recovery SSH user was provided without recovery authorized keys",
    ):
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


def test_build_rpi4b_image_rejects_default_recovery_username_without_keys(
    tmp_path: Path,
) -> None:
    """Regression: explicitly supplied default recovery user without keys should fail fast."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(
        ImagerBuildError,
        match="Recovery SSH user was provided without recovery authorized keys",
    ):
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


def test_build_rpi4b_image_requires_recovery_ssh_unless_explicitly_skipped(
    tmp_path: Path,
) -> None:
    """Regression: service-layer callers must not bypass recovery SSH requirements."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")

    with pytest.raises(ImagerBuildError, match="Recovery SSH is required"):
        build_rpi4b_image(
            name="recovery-service-required",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
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

    with (
        patch("apps.imager.services._customize_image"),
        patch(
            "apps.imager.services._ensure_image_minimum_size",
            side_effect=no_op_image_size_adjustment,
        ) as sizing_mock,
    ):
        result = build_rpi4b_image(
            name="stable",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="https://cdn.example.com/images",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            skip_recovery_ssh=True,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="stable")
    assert (
        sizing_mock.call_args.kwargs["minimum_size_bytes"]
        == DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES
    )
    assert result.output_path.exists()
    assert artifact.sha256 == result.sha256
    assert artifact.download_uri == "https://cdn.example.com/images/stable-rpi-4b.img"
    assert artifact.metadata["recovery_ssh"] == {
        "enabled": False,
        "user": "",
        "authorized_key_count": 0,
        "explicitly_skipped": True,
    }
    assert artifact.metadata["suite_bundle"] == {"enabled": False}
    assert artifact.metadata["host_network_profiles"] == {
        "enabled": False,
        "count": 0,
        "profiles": [],
    }
    assert (
        artifact.metadata["image_size"]["minimum_size_bytes"]
        == DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES
    )
    assert artifact.metadata["image_size"]["root_partition_expanded"] is True


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
            minimum_image_size_bytes=0,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="recovery-enabled")
    assert artifact.metadata["recovery_ssh"] == {
        "enabled": True,
        "user": "arthe",
        "authorized_key_count": 1,
        "explicitly_skipped": False,
    }


@pytest.mark.django_db
def test_build_rpi4b_image_persists_suite_and_network_metadata(tmp_path: Path) -> None:
    """Regression: build records static bundle and host network injection metadata."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")
    suite_source = make_suite_source(tmp_path)
    network_profile = NetworkProfileInfo(
        name="Home WiFi",
        filename="home.nmconnection",
        source_path=tmp_path / "home.nmconnection",
        remote_path="/etc/NetworkManager/system-connections/home.nmconnection",
    )
    customization_result = ImageCustomizationResult(
        suite_bundle=SuiteBundleInfo(
            source_path=suite_source,
            remote_path="/usr/local/share/arthexis/arthexis-suite.tar.gz",
            sha256="bundle123",
            size_bytes=1234,
            file_count=5,
        ),
        network_profiles=(network_profile,),
    )

    with patch(
        "apps.imager.services._customize_image", return_value=customization_result
    ):
        build_rpi4b_image(
            name="bundled",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            recovery_authorized_keys=[
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestRecovery recovery",
            ],
            suite_source_path=suite_source,
            minimum_image_size_bytes=0,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="bundled")
    assert artifact.metadata["suite_bundle"]["enabled"] is True
    assert artifact.metadata["suite_bundle"]["sha256"] == "bundle123"
    assert artifact.metadata["host_network_profiles"]["profiles"] == [
        {
            "name": "Home WiFi",
            "filename": "home.nmconnection",
            "remote_path": "/etc/NetworkManager/system-connections/home.nmconnection",
        }
    ]


@pytest.mark.django_db
def test_build_rpi4b_image_writes_recovery_ap_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: operators get the provisioned recovery AP PSK beside the image."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")
    monkeypatch.setenv("ARTHEXIS_RECOVERY_AP_PSK", "known-recovery-passphrase")
    customization_result = ImageCustomizationResult(
        recovery_ap_psk_path="/etc/arthexis/recovery-ap.psk"
    )

    with patch(
        "apps.imager.services._customize_image", return_value=customization_result
    ) as customize_mock:
        build_rpi4b_image(
            name="recovery-ap",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            skip_recovery_ssh=True,
            minimum_image_size_bytes=0,
        )

    customize_mock.assert_called_once()
    assert (
        customize_mock.call_args.kwargs["recovery_ap_psk"]
        == "known-recovery-passphrase"
    )
    artifact = RaspberryPiImageArtifact.objects.get(name="recovery-ap")
    sidecar_path = Path(artifact.metadata["recovery_ap"]["psk_sidecar"])
    assert sidecar_path.name == "recovery-ap-rpi-4b.img.recovery-ap.psk"
    assert sidecar_path.read_text(encoding="utf-8") == "known-recovery-passphrase\n"
    assert sidecar_path.stat().st_mode & 0o777 == 0o600
    assert artifact.metadata["recovery_ap"] == {
        "enabled_for_roles": ["Satellite", "Control"],
        "ssid_template": "arthexis-<reserved node number>",
        "psk_provisioned": True,
        "psk_path": "/etc/arthexis/recovery-ap.psk",
        "psk_sidecar": str(sidecar_path),
    }
    assert "known-recovery-passphrase" not in json.dumps(artifact.metadata)


@pytest.mark.django_db
def test_build_rpi4b_image_validates_base_before_upstream_reservation(
    tmp_path: Path,
) -> None:
    missing_base_image = tmp_path / "missing.img"

    with patch("apps.imager.reservations.urlopen") as open_mock:
        with pytest.raises(ImagerBuildError, match="Base image does not exist"):
            build_rpi4b_image(
                name="gway-missing-base",
                base_image_uri=str(missing_base_image),
                output_dir=tmp_path,
                download_base_uri="",
                git_url="https://github.com/arthexis/arthexis.git",
                customize=True,
                skip_recovery_ssh=True,
                reserve_node=True,
                reserve_hostname_prefix="gway",
                next_number_base_url="https://registration.example.test",
                minimum_image_size_bytes=0,
            )

    open_mock.assert_not_called()


@pytest.mark.django_db
def test_build_rpi4b_image_reserves_peer_node(tmp_path: Path) -> None:
    """A reserved build should create a peer placeholder after the artifact succeeds."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")
    reservation = ImageReservation(
        hostname="gway-004",
        hostname_prefix="gway",
        number=4,
        ipv4_address="10.42.0.4",
        network_cidr="10.42.0.0/16",
        parent_hostname="gway-001",
        claim_token="claim-token",
    )
    customization_result = ImageCustomizationResult(reservation=reservation)

    with (
        patch("apps.imager.services.plan_image_reservation", return_value=reservation),
        patch(
            "apps.imager.services._customize_image", return_value=customization_result
        ),
    ):
        result = build_rpi4b_image(
            name="gway-004-repair",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            skip_recovery_ssh=True,
            reserve_node=True,
            reserve_number=4,
            minimum_image_size_bytes=0,
        )

    node = Node.objects.get(hostname="gway-004")
    artifact = RaspberryPiImageArtifact.objects.get(name="gway-004-repair")
    assert node.reserved is True
    assert node.current_relation == Node.Relation.PEER
    assert node.address == "10.42.0.4"
    stored_hash = node.mesh_key_fingerprint_metadata[RESERVATION_CLAIM_TOKEN_HASH_KEY]
    assert check_password("claim-token", stored_hash)
    assert result.reservation["hostname"] == "gway-004"
    assert "claim_token" not in result.reservation
    assert result.reservation["claim_token_baked"] is True
    assert result.reservation["node_id"] == node.id
    assert artifact.metadata["reserved_node"]["enabled"] is True
    assert artifact.metadata["reserved_node"]["hostname"] == "gway-004"


@pytest.mark.django_db
def test_build_rpi4b_image_rejects_reserving_active_node_hostname(
    tmp_path: Path,
) -> None:
    """A reservation must not overwrite an existing active node row."""

    base_image = tmp_path / "base.img"
    base_image.write_bytes(b"raspberrypi")
    Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        current_relation=Node.Relation.SELF,
        reserved=False,
    )
    reservation = ImageReservation(
        hostname="gway-004",
        hostname_prefix="gway",
        number=4,
        ipv4_address="10.42.0.44",
        network_cidr="10.42.0.0/16",
        parent_hostname="gway-001",
    )
    customization_result = ImageCustomizationResult(reservation=reservation)

    with (
        patch("apps.imager.services.plan_image_reservation", return_value=reservation),
        patch(
            "apps.imager.services._customize_image", return_value=customization_result
        ),
        pytest.raises(ImagerBuildError, match="already used by active node"),
    ):
        build_rpi4b_image(
            name="gway-004-repair",
            base_image_uri=str(base_image),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            skip_recovery_ssh=True,
            reserve_node=True,
            reserve_number=4,
            minimum_image_size_bytes=0,
        )

    node = Node.objects.get(hostname="gway-004")
    assert node.reserved is False
    assert node.current_relation == Node.Relation.SELF
    assert node.address == "10.42.0.4"
    assert not RaspberryPiImageArtifact.objects.filter(name="gway-004-repair").exists()


@pytest.mark.django_db
def test_plan_image_reservation_uses_next_prefix_number_and_numbered_ip(
    monkeypatch,
) -> None:
    """The default reservation for gway-001 after gway-005 should be gway-006."""

    Node.objects.create(hostname="gway-001")
    Node.objects.create(hostname="gway-005")
    monkeypatch.setenv("IMAGER_RESERVE_HOSTNAME_PREFIX", "gway")
    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setattr("apps.imager.reservations._known_neighbor_ips", lambda: set())
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    reservation = plan_image_reservation()

    assert reservation.hostname == "gway-006"
    assert reservation.number == 6
    assert reservation.ipv4_address == "10.42.0.6"
    assert reservation.downstream_registration_base_url == ""


@pytest.mark.django_db
def test_plan_image_reservation_uses_upstream_next_gway_number(
    monkeypatch,
) -> None:
    """The imager can ask an explicit upstream for the next GWAY number."""

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"next_number": 12, "claim_token": "claim-token"}).encode(
                "utf-8"
            )

    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setenv("IMAGER_GWAY_RESERVATION_TOKEN", "reservation-token")
    monkeypatch.setattr("apps.imager.reservations._known_neighbor_ips", lambda: set())
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    with patch(
        "apps.imager.reservations.urlopen", return_value=FakeResponse()
    ) as open_mock:
        reservation = plan_image_reservation(
            hostname_prefix="gway",
            next_number_base_url="https://registration.example.test",
        )

    request = open_mock.call_args.args[0]
    assert request.get_method() == "POST"
    assert (
        request.full_url
        == "https://registration.example.test/nodes/register/next-gway-number/"
    )
    assert request.data.decode("utf-8") == "prefix=gway&minimum_number=1"
    assert request.get_header("Authorization") == "Bearer reservation-token"
    assert reservation.hostname == "gway-012"
    assert reservation.number == 12
    assert reservation.claim_token == "claim-token"
    assert reservation.ipv4_address == "10.42.0.12"
    assert reservation.downstream_registration_base_url == ""


@pytest.mark.django_db
def test_plan_image_reservation_sends_local_minimum_to_upstream(
    monkeypatch,
) -> None:
    """Remote reservations should not be orphaned when local numbering is ahead."""

    Node.objects.create(hostname="gway-015")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"next_number": 18, "claim_token": "claim-token"}).encode(
                "utf-8"
            )

    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setattr("apps.imager.reservations._known_neighbor_ips", lambda: set())
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    with patch(
        "apps.imager.reservations.urlopen", return_value=FakeResponse()
    ) as open_mock:
        reservation = plan_image_reservation(
            hostname_prefix="gway",
            next_number_base_url="https://registration.example.test",
        )

    request = open_mock.call_args.args[0]
    assert request.get_method() == "POST"
    assert (
        request.full_url
        == "https://registration.example.test/nodes/register/next-gway-number/"
    )
    assert request.data.decode("utf-8") == "prefix=gway&minimum_number=16"
    assert reservation.hostname == "gway-018"
    assert reservation.number == 18
    assert reservation.claim_token == "claim-token"


@pytest.mark.django_db
def test_plan_image_reservation_fails_when_upstream_number_lookup_fails(
    monkeypatch,
) -> None:
    Node.objects.create(hostname="gway-001")
    Node.objects.create(hostname="gway-005")
    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setattr("apps.imager.reservations._known_neighbor_ips", lambda: set())
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    with patch("apps.imager.reservations.urlopen", side_effect=OSError("offline")):
        with pytest.raises(RemoteReservationError, match="Could not reserve"):
            plan_image_reservation(
                hostname_prefix="gway",
                next_number_base_url="https://registration.example.test",
            )


@pytest.mark.django_db
def test_plan_image_reservation_manual_number_skips_upstream_lookup(
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setattr("apps.imager.reservations._known_neighbor_ips", lambda: set())
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    with patch("apps.imager.reservations.urlopen") as open_mock:
        reservation = plan_image_reservation(
            hostname_prefix="gway",
            number=4,
            next_number_base_url="https://registration.example.test",
        )

    open_mock.assert_not_called()
    assert reservation.hostname == "gway-004"
    assert reservation.number == 4
    assert reservation.claim_token


@pytest.mark.django_db
def test_plan_image_reservation_rebuild_keeps_reserved_numbered_ip_and_role(
    monkeypatch,
) -> None:
    """Rebuilding an existing reservation should not treat its own IP as unavailable."""

    satellite = NodeRole.objects.create(name="Satellite")
    Node.objects.create(hostname="gway-001")
    Node.objects.create(
        hostname="gway-003",
        address="10.42.0.3",
        ipv4_address="10.42.0.3",
        reserved=True,
        role=satellite,
    )
    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setattr(
        "apps.imager.reservations._known_neighbor_ips",
        lambda: {"10.42.0.3"},
    )
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    reservation = plan_image_reservation(hostname_prefix="gway", number=3)

    assert reservation.hostname == "gway-003"
    assert reservation.number == 3
    assert reservation.ipv4_address == "10.42.0.3"
    assert reservation.role_name == "Satellite"


@pytest.mark.django_db
def test_plan_image_reservation_explicit_role_overrides_existing_reserved_role(
    monkeypatch,
) -> None:
    """An explicit role remains a deliberate rebuild override."""

    satellite = NodeRole.objects.create(name="Satellite")
    Node.objects.create(hostname="gway-001")
    Node.objects.create(
        hostname="gway-003",
        address="10.42.0.3",
        ipv4_address="10.42.0.3",
        reserved=True,
        role=satellite,
    )
    monkeypatch.setenv("IMAGER_RESERVE_NETWORK_CIDR", "10.42.0.0/16")
    monkeypatch.setattr("apps.imager.reservations._known_neighbor_ips", lambda: set())
    monkeypatch.setattr("apps.imager.reservations.psutil.net_if_addrs", lambda: {})

    reservation = plan_image_reservation(
        hostname_prefix="gway",
        number=3,
        role_name="Control",
    )

    assert reservation.ipv4_address == "10.42.0.3"
    assert reservation.role_name == "Control"


def test_render_reservation_env_includes_downstream_registration_base_url() -> None:
    reservation = ImageReservation(
        hostname="gway-004",
        hostname_prefix="gway",
        number=4,
        ipv4_address="10.42.0.4",
        network_cidr="10.42.0.0/16",
        parent_hostname="gway-001",
        role_name="Satellite",
        downstream_registration_base_url="https://registration.example.test",
        claim_token="claim-token",
    )

    content = render_reservation_env(reservation)

    assert "NODE_HOSTNAME=gway-004" in content
    assert "NODE_ROLE=Satellite" in content
    assert (
        "ARTHEXIS_DOWNSTREAM_REGISTRATION_BASE_URL=https://registration.example.test"
        in content
    )
    assert "NODE_RESERVED_CLAIM_TOKEN=claim-token" in content


def test_active_parent_network_names_include_hyperline_and_skip_recovery_ap(
    monkeypatch,
) -> None:
    monkeypatch.setattr("apps.imager.reservations.shutil_which", lambda name: "nmcli")

    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            "hyperline:802-3-ethernet:eth0\n"
            "shop-wifi:wifi:wlan0\n"
            "arthexis-3:wifi:wlan1\n"
            "lo:loopback:lo\n"
        ),
    )
    monkeypatch.setattr(
        "apps.imager.reservations.subprocess.run",
        lambda *a, **k: completed,
    )

    assert active_parent_network_names() == ["hyperline", "shop-wifi"]


@pytest.mark.django_db
def test_watch_reserved_nodes_observes_matching_node_without_claiming(
    monkeypatch,
) -> None:
    """The watcher reports matches but leaves trust to signed registration."""

    node = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
    )

    def fake_fetch(host: str, ports: tuple[int, ...], timeout: float):
        assert ports == (8888,)
        if host != "10.42.0.4":
            return None
        return {
            "hostname": "gway-004",
            "address": "10.42.0.4",
            "ipv4_address": "10.42.0.4",
            "port": "not-a-port",
            "mac_address": "aa:bb:cc:dd:ee:04",
            "_watch_host": host,
            "_watch_port": 8888,
        }

    monkeypatch.setattr("apps.imager.reservations._fetch_node_info", fake_fetch)

    results = watch_reserved_nodes_once(interfaces=[], ports=(8888,), timeout=0.1)

    assert results[0].status == "observed"
    assert "waiting for signed registration" in results[0].detail
    node.refresh_from_db()
    assert node.reserved is True
    assert node.ipv4_address == "10.42.0.4"
    assert node.mac_address == ""
    assert node.port == 8888
    assert node.trusted is False


def test_fetch_node_info_brackets_ipv6_hosts_and_reads_full_json() -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"hostname": "gway-006", "note": "x" * 9000}).encode(
                "utf-8"
            )

    with patch(
        "apps.imager.reservations.urlopen", return_value=FakeResponse()
    ) as open_mock:
        payload = _fetch_node_info("fd00::6", (8888,), timeout=0.1)

    request = open_mock.call_args.args[0]
    assert request.full_url == "http://[fd00::6]:8888/nodes/info/"
    assert payload["hostname"] == "gway-006"
    assert len(payload["note"]) == 9000


@pytest.mark.django_db
def test_watch_reserved_nodes_rejects_hostname_mismatch(monkeypatch) -> None:
    """A different node at the reserved IP must not claim the reservation."""

    node = Node.objects.create(
        hostname="gway-004",
        address="10.42.0.4",
        ipv4_address="10.42.0.4",
        current_relation=Node.Relation.PEER,
        reserved=True,
    )

    def fake_fetch(host: str, ports: tuple[int, ...], timeout: float):
        if host != "10.42.0.4":
            return None
        return {
            "hostname": "gway-099",
            "address": "10.42.0.4",
            "ipv4_address": "10.42.0.4",
            "port": 8888,
            "_watch_host": host,
        }

    monkeypatch.setattr("apps.imager.reservations._fetch_node_info", fake_fetch)

    results = watch_reserved_nodes_once(interfaces=[], ports=(8888,), timeout=0.1)

    assert results[0].status == "pending"
    node.refresh_from_db()
    assert node.reserved is True


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
            skip_recovery_ssh=True,
            minimum_image_size_bytes=0,
        )

    assert result.output_path.read_bytes() == source_bytes


@pytest.mark.django_db
def test_build_rpi4b_image_persists_connect_ota_engine_profile_metadata(
    tmp_path: Path,
) -> None:
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
            skip_recovery_ssh=True,
            minimum_image_size_bytes=0,
        )

    artifact = RaspberryPiImageArtifact.objects.get(name="connect-stable")
    assert artifact.build_engine == "arthexis-bootstrap"
    assert artifact.build_profile == "connect-ota"
    assert (
        artifact.metadata["profile_manifest"]["compatibility_model"] == "raspberry-pi-4"
    )


@pytest.mark.django_db
def test_build_rpi4b_image_rejects_connect_ota_profile_when_manifest_fields_missing(
    tmp_path: Path,
) -> None:
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
            skip_recovery_ssh=True,
            minimum_image_size_bytes=0,
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
            skip_recovery_ssh=True,
            minimum_image_size_bytes=0,
        )

    assert result.output_path.exists()
    assert result.output_path.read_bytes() == source_bytes


@pytest.mark.django_db
@override_settings(IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS=True)
@patch("apps.imager.services.socket.getaddrinfo")
def test_build_rpi4b_image_blocks_private_remote_host(
    getaddrinfo_mock, tmp_path: Path
) -> None:
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
def test_validate_remote_base_image_url_allows_private_host_when_allowlisted(
    getaddrinfo_mock,
) -> None:
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
            vendor="Generic",
            model="SD Reader",
            serial="1234",
            identity_paths=["/dev/disk/by-id/usb-sd-reader-1234"],
        )
    ]

    out = StringIO()
    call_command("imager", "devices", stdout=out)
    output = out.getvalue()

    assert "/dev/sda" in output
    assert "removable=yes" in output
    assert "protected=no" in output
    assert "vendor=Generic" in output
    assert "model=SD Reader" in output
    assert "identity_paths=/dev/disk/by-id/usb-sd-reader-1234" in output
    assert "write_blocked=(none)" in output


def _burn_test_device(
    *,
    path: str = "/dev/sdb",
    by_id_path: str = "/dev/disk/by-id/usb-sd-reader-1234",
    size_bytes: int = 1024,
    removable: bool = True,
    serial: str = "1234",
) -> BlockDeviceInfo:
    return BlockDeviceInfo(
        path=path,
        size_bytes=size_bytes,
        transport="usb",
        removable=removable,
        mountpoints=[],
        partitions=[f"{path}1"],
        protected=False,
        vendor="Generic",
        model="SD Reader",
        serial=serial,
        identity_paths=[by_id_path],
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("device_path", "device", "message"),
    [
        (
            "/dev/sdb",
            _burn_test_device(),
            "stable /dev/disk/by-id/",
        ),
        (
            "/dev/disk/by-id/../../sdb",
            _burn_test_device(),
            "stable /dev/disk/by-id/",
        ),
        (
            "/dev/disk/by-id/usb-other-reader",
            _burn_test_device(
                path="/dev/disk/by-id/usb-other-reader",
                by_id_path="/dev/disk/by-id/usb-sd-reader-1234",
            ),
            "discovered /dev/disk/by-id/",
        ),
        (
            "/dev/disk/by-id/usb-sd-reader-1234",
            _burn_test_device(removable=False),
            "non-removable",
        ),
        (
            "/dev/disk/by-id/usb-sd-reader-1234",
            _burn_test_device(serial=""),
            "serial identity",
        ),
    ],
)
def test_burn_queue_requires_stable_removable_serial_identity(
    device_path: str,
    device: BlockDeviceInfo,
    message: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.img"
    source.write_bytes(b"image")

    with (
        patch("apps.imager.burner.list_block_devices", return_value=[device]),
        pytest.raises(ImagerBuildError, match=message),
    ):
        queue_burn_job(image_path=str(source), device_path=device_path)


@pytest.mark.django_db
@patch("apps.imager.burner.list_block_devices")
def test_imager_burn_queue_records_status_and_identity(
    list_devices_mock, tmp_path: Path
) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    list_devices_mock.return_value = [_burn_test_device(by_id_path=by_id_path)]

    out = StringIO()
    call_command(
        "imager",
        "burn",
        "queue",
        "--image",
        str(source),
        "--device",
        by_id_path,
        stdout=out,
    )

    job = RaspberryPiImageBurnJob.objects.get()
    output = out.getvalue()
    assert job.device_path == by_id_path
    assert job.device_identity["requested_path"] == by_id_path
    assert job.device_identity["serial"] == "1234"
    assert "Queued burn job" in output
    assert f"device={by_id_path}" in output
    assert "source_sha256=" in output

    status_out = StringIO()
    call_command("imager", "burn", "status", str(job.uuid), "--log", stdout=status_out)
    status_output = status_out.getvalue()
    assert f"status={RaspberryPiImageBurnJob.Status.QUEUED}" in status_output
    assert "queued source=" in status_output


@pytest.mark.django_db
def test_claim_next_burn_job_fails_stale_running_jobs_before_claim(
    tmp_path: Path,
) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    device = _burn_test_device(by_id_path=by_id_path)
    with patch("apps.imager.burner.list_block_devices", return_value=[device]):
        stale_job = queue_burn_job(image_path=str(source), device_path=by_id_path)
        queued_job = queue_burn_job(image_path=str(source), device_path=by_id_path)

    RaspberryPiImageBurnJob.objects.filter(pk=stale_job.pk).update(
        status=RaspberryPiImageBurnJob.Status.RUNNING,
        updated_at=timezone.now() - timedelta(hours=7),
    )

    claimed = claim_next_burn_job()

    stale_job.refresh_from_db()
    assert stale_job.status == RaspberryPiImageBurnJob.Status.FAILED
    assert "heartbeat expired" in stale_job.error
    assert claimed is not None
    assert claimed.pk == queued_job.pk
    assert claimed.status == RaspberryPiImageBurnJob.Status.RUNNING


@pytest.mark.django_db
def test_burn_job_claim_is_atomic_for_duplicate_claimers(tmp_path: Path) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    device = _burn_test_device(by_id_path=by_id_path)
    with patch("apps.imager.burner.list_block_devices", return_value=[device]):
        job = queue_burn_job(image_path=str(source), device_path=by_id_path)

    now = timezone.now()
    assert _claim_burn_job(job.pk, now=now) is True
    assert _claim_burn_job(job.pk, now=now) is False

    job.refresh_from_db()
    assert job.status == RaspberryPiImageBurnJob.Status.RUNNING
    assert job.attempts == 1


@pytest.mark.django_db
def test_burn_job_progress_heartbeat_refreshes_running_job(tmp_path: Path) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    device = _burn_test_device(by_id_path=by_id_path)
    with patch("apps.imager.burner.list_block_devices", return_value=[device]):
        job = queue_burn_job(image_path=str(source), device_path=by_id_path)

    old_updated_at = timezone.now() - timedelta(hours=1)
    RaspberryPiImageBurnJob.objects.filter(pk=job.pk).update(
        status=RaspberryPiImageBurnJob.Status.RUNNING,
        updated_at=old_updated_at,
    )

    _job_progress_heartbeat(job.pk)(written_bytes=1, source_size=2)

    job.refresh_from_db()
    assert job.updated_at > old_updated_at
    assert job.progress_bytes == 1
    assert job.progress_total_bytes == 2
    assert job.progress_percent == 50


@pytest.mark.django_db
def test_run_burn_job_heartbeats_during_queued_source_revalidation(
    tmp_path: Path,
) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    device = _burn_test_device(by_id_path=by_id_path)
    with patch("apps.imager.burner.list_block_devices", return_value=[device]):
        job = queue_burn_job(image_path=str(source), device_path=by_id_path)

    old_updated_at = timezone.now() - timedelta(hours=1)
    RaspberryPiImageBurnJob.objects.filter(pk=job.pk).update(
        status=RaspberryPiImageBurnJob.Status.RUNNING,
        updated_at=old_updated_at,
    )

    result = WriteResult(
        device_path=by_id_path,
        image_path=source,
        size_bytes=source.stat().st_size,
        source_sha256=job.image_sha256,
        written_sha256=job.image_sha256,
        verified=True,
        backup=None,
    )

    def _write_image_with_progress_check(**_kwargs):
        job.refresh_from_db()
        assert job.progress_bytes == 0
        return result

    with (
        patch("apps.imager.burner.list_block_devices", return_value=[device]),
        patch("apps.imager.burner.quiet_usb_pollers", return_value=nullcontext()),
        patch(
            "apps.imager.burner.write_image_to_device",
            side_effect=_write_image_with_progress_check,
        ),
    ):
        run_burn_job(job)

    job.refresh_from_db()
    assert job.updated_at > old_updated_at
    assert job.progress_bytes == source.stat().st_size
    assert job.progress_total_bytes == source.stat().st_size


@pytest.mark.django_db
def test_run_burn_job_revalidates_identity_before_write(tmp_path: Path) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    with patch(
        "apps.imager.burner.list_block_devices",
        return_value=[_burn_test_device(by_id_path=by_id_path)],
    ):
        job = queue_burn_job(image_path=str(source), device_path=by_id_path)

    changed_device = _burn_test_device(by_id_path=by_id_path, serial="5678")
    with (
        patch("apps.imager.burner.list_block_devices", return_value=[changed_device]),
        patch("apps.imager.burner.write_image_to_device") as write_mock,
    ):
        run_burn_job(job)

    job.refresh_from_db()
    write_mock.assert_not_called()
    assert job.status == RaspberryPiImageBurnJob.Status.FAILED
    assert "serial changed" in job.error


@pytest.mark.django_db
def test_run_burn_job_writes_by_id_path_and_persists_result(tmp_path: Path) -> None:
    by_id_path = "/dev/disk/by-id/usb-sd-reader-1234"
    source = tmp_path / "source.img"
    source.write_bytes(b"image")
    device = _burn_test_device(by_id_path=by_id_path)
    with patch("apps.imager.burner.list_block_devices", return_value=[device]):
        job = queue_burn_job(image_path=str(source), device_path=by_id_path)

    result = WriteResult(
        device_path=by_id_path,
        image_path=source,
        size_bytes=source.stat().st_size,
        source_sha256=job.image_sha256,
        written_sha256=job.image_sha256,
        verified=True,
        backup=None,
    )

    quiet_active = False

    @contextmanager
    def quiet_window(**_kwargs):
        nonlocal quiet_active
        quiet_active = True
        try:
            yield
        finally:
            quiet_active = False

    def list_devices_inside_quiet_window():
        assert quiet_active is True
        return [device]

    def write_inside_quiet_window(**_kwargs):
        assert quiet_active is True
        return result

    with (
        patch(
            "apps.imager.burner.list_block_devices",
            side_effect=list_devices_inside_quiet_window,
        ),
        patch(
            "apps.imager.burner.quiet_usb_pollers",
            side_effect=quiet_window,
        ) as quiet_mock,
        patch(
            "apps.imager.burner.write_image_to_device",
            side_effect=write_inside_quiet_window,
        ) as write_mock,
    ):
        run_burn_job(job)

    job.refresh_from_db()
    quiet_mock.assert_called_once()
    quiet_kwargs = quiet_mock.call_args.kwargs
    assert "enabled" not in quiet_kwargs
    assert callable(quiet_kwargs.get("log"))
    write_mock.assert_called_once()
    assert write_mock.call_args.kwargs["device_path"] == by_id_path
    assert job.status == RaspberryPiImageBurnJob.Status.SUCCEEDED
    assert job.result["verified"] is True
    assert job.result["written_sha256"] == job.image_sha256


def test_guestfish_write_scopes_temp_dirs_to_image_output_directory(
    tmp_path: Path,
) -> None:
    """Regression: guestfish temp dir should be scoped and cleaned while cache persists."""

    image_path = tmp_path / "artifact.img"
    image_path.write_bytes(b"img")
    local_path = tmp_path / "bootstrap.sh"
    local_path.write_text("#!/bin/sh\n", encoding="utf-8")
    guestfish_result = SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch(
        "apps.imager.services.subprocess.run", return_value=guestfish_result
    ) as run_mock:
        _guestfish_write(
            image_path,
            local_path,
            "/usr/local/bin/arthexis-bootstrap.sh",
            chmod_mode="0755",
        )

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
def test_build_rpi4b_image_rejects_corrupted_archives(
    tmp_path: Path, extension: str, writer
) -> None:
    """Regression: malformed compressed base images should raise a user-facing build error."""

    compressed_source = tmp_path / f"base{extension}"
    writer(compressed_source)

    with (
        patch("apps.imager.services._customize_image"),
        pytest.raises(ImagerBuildError, match="invalid or corrupted"),
    ):
        build_rpi4b_image(
            name=f"corrupt-{extension.replace('.', '-')}",
            base_image_uri=str(compressed_source),
            output_dir=tmp_path,
            download_base_uri="",
            git_url="https://github.com/arthexis/arthexis.git",
            customize=True,
            skip_recovery_ssh=True,
        )


@pytest.mark.django_db
def test_prepare_image_serve_updates_artifact_download_url(tmp_path: Path) -> None:
    """Regression: local serving should produce and persist a deployment URL."""

    output_path = tmp_path / "stable-rpi-4b.img"
    output_path.write_bytes(b"raspberrypi")
    artifact = RaspberryPiImageArtifact.objects.create(
        name="stable",
        target=TARGET_RPI4B,
        base_image_uri=str(output_path),
        output_filename=output_path.name,
        output_path=str(output_path),
        sha256="",
        size_bytes=output_path.stat().st_size,
        download_uri="",
        metadata={},
    )

    result = prepare_image_serve(
        artifact_name="stable",
        host="0.0.0.0",
        port=8090,
        url_host="10.42.0.138",
    )

    artifact.refresh_from_db()
    assert isinstance(result, ServeResult)
    assert result.url == "http://10.42.0.138:8090/stable-rpi-4b.img"
    assert artifact.download_uri == result.url
    assert artifact.metadata["local_serve"]["url"] == result.url


def test_build_served_artifact_url_uses_base_url_and_quotes_filename() -> None:
    """Regression: deployment URLs should be safe for filenames with spaces."""

    assert (
        _build_served_artifact_url(
            output_filename="Raspberry Pi OS.img",
            port=8090,
            base_url="https://downloads.example.com/images/",
        )
        == "https://downloads.example.com/images/Raspberry%20Pi%20OS.img"
    )


@patch("apps.imager.management.commands.imager.serve_image_file")
def test_imager_serve_command_prints_artifact_url(serve_mock, tmp_path: Path) -> None:
    """Regression: the serve subcommand should expose the computed artifact URL."""

    image_path = tmp_path / "stable-rpi-4b.img"
    image_path.write_bytes(b"raspberrypi")
    out = StringIO()

    call_command(
        "imager",
        "serve",
        "--image-path",
        str(image_path),
        "--host",
        "127.0.0.1",
        "--port",
        "8090",
        "--url-host",
        "10.42.0.138",
        stdout=out,
    )

    assert "artifact_url=http://10.42.0.138:8090/stable-rpi-4b.img" in out.getvalue()
    serve_mock.assert_called_once()


@patch("apps.imager.management.commands.imager.write_image_to_device")
def test_imager_write_command_passes_backup_options_and_prints_backup_metadata(
    write_mock, tmp_path: Path
) -> None:
    """Regression: write --backup should expose verified backup metadata."""

    backup_path = tmp_path / "backups" / "dev-sdb.img"
    write_mock.return_value = SimpleNamespace(
        device_path="/dev/sdb",
        image_path=tmp_path / "artifact.img",
        size_bytes=13,
        source_sha256="source",
        written_sha256="source",
        verified=True,
        backup=WriteBackupResult(
            path=backup_path,
            size_bytes=32,
            sha256="backup",
            verified=True,
        ),
    )

    out = StringIO()
    with patch(
        "apps.imager.management.commands.imager.quiet_usb_pollers",
        return_value=nullcontext(),
    ) as quiet_mock:
        call_command(
            "imager",
            "write",
            "--artifact",
            "stable",
            "--device",
            "/dev/sdb",
            "--yes",
            "--backup",
            "--backup-dir",
            str(tmp_path / "backups"),
            stdout=out,
        )

    quiet_mock.assert_called_once()
    quiet_kwargs = quiet_mock.call_args.kwargs
    assert "enabled" not in quiet_kwargs
    assert callable(quiet_kwargs.get("log"))
    write_mock.assert_called_once_with(
        device_path="/dev/sdb",
        artifact_name="stable",
        image_path="",
        confirmed=True,
        backup=True,
        backup_dir=tmp_path / "backups",
    )
    output = out.getvalue()
    assert f"backup_path={backup_path}" in output
    assert "backup_size_bytes=32" in output
    assert "backup_sha256=backup" in output
    assert "backup_verified=yes" in output


@patch("apps.imager.management.commands.imager.write_image_to_device")
def test_imager_write_command_can_disable_windows_automount_guard(
    write_mock, tmp_path: Path
) -> None:
    """Regression: operators can opt out when an external Windows guard is active."""

    image_path = tmp_path / "artifact.img"
    write_mock.return_value = SimpleNamespace(
        device_path="/dev/sdb",
        image_path=image_path,
        size_bytes=13,
        source_sha256="source",
        written_sha256="source",
        verified=True,
        backup=None,
    )

    out = StringIO()
    with patch(
        "apps.imager.management.commands.imager.quiet_usb_pollers",
        return_value=nullcontext(),
    ):
        call_command(
            "imager",
            "write",
            "--image-path",
            str(image_path),
            "--device",
            "/dev/sdb",
            "--yes",
            "--no-windows-automount-guard",
            stdout=out,
        )

    assert write_mock.call_args.kwargs["windows_automount_guard"] is False


@patch(
    "apps.imager.management.commands.imager.serve_image_file",
    side_effect=OSError("port in use"),
)
def test_imager_serve_command_reports_server_startup_errors(
    _serve_mock, tmp_path: Path
) -> None:
    """Regression: serve startup failures should be clean command errors."""

    image_path = tmp_path / "stable-rpi-4b.img"
    image_path.write_bytes(b"raspberrypi")

    with pytest.raises(CommandError, match="Could not start image server: port in use"):
        call_command(
            "imager",
            "serve",
            "--image-path",
            str(image_path),
            "--host",
            "127.0.0.1",
            "--port",
            "8090",
        )


@patch("apps.imager.services.urlopen")
@patch("apps.imager.services.subprocess.run")
@patch("apps.imager.services.shutil.which", return_value="/usr/bin/ssh")
@patch("apps.imager.services.socket.create_connection")
def test_test_rpi_access_checks_ssh_and_http(
    create_connection_mock,
    _which_mock,
    run_mock,
    urlopen_mock,
) -> None:
    """Regression: post-burn access checks should cover recovery SSH and suite HTTP."""

    create_connection_mock.return_value.__enter__.return_value = None
    run_mock.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
    urlopen_mock.return_value.__enter__.return_value = SimpleNamespace(
        getcode=lambda: 200
    )

    result = run_rpi_access_test(
        host="10.42.0.50",
        ssh_user="arthe",
        http_url="http://10.42.0.50:8888/login/",
        timeout=1,
    )

    assert result.ok is True
    assert [check.name for check in result.checks] == ["ssh-tcp", "ssh-auth", "http"]
    assert all(isinstance(check, AccessCheckResult) for check in result.checks)


@patch("apps.imager.services.urlopen")
def test_test_rpi_access_brackets_ipv6_default_http_url(urlopen_mock) -> None:
    """Regression: IPv6 hosts need brackets in default HTTP test URLs."""

    urlopen_mock.return_value.__enter__.return_value = SimpleNamespace(
        getcode=lambda: 200
    )

    result = run_rpi_access_test(
        host="2001:db8::50",
        skip_ssh=True,
        timeout=1,
    )

    assert result.ok is True
    request = urlopen_mock.call_args.args[0]
    assert request.full_url == "http://[2001:db8::50]:8888/"


def _record_windows_automount_commands(
    command_calls: list[list[str]],
    scripts: list[str],
    *,
    automount_enabled: bool = True,
    fail_restore: bool = False,
):
    def _tool_name(args: list[str]) -> str:
        return PureWindowsPath(args[0]).name.casefold()

    def _side_effect(args, **_kwargs):
        command_calls.append(list(args))
        tool_name = _tool_name(args)
        if fail_restore and tool_name == "mountvol.exe" and args[1:2] == ["/E"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="simulated restore failure",
            )
        if tool_name == "diskpart.exe":
            script = Path(args[2]).read_text(encoding="ascii")
            scripts.append(script)
            if script == "automount\nexit\n":
                state = "enabled" if automount_enabled else "disabled"
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"Automatic mounting of new volumes {state}.\n",
                    stderr="",
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return _side_effect


def _windows_automount_command_summary(
    command_calls: list[list[str]],
) -> list[list[str]]:
    return [
        [PureWindowsPath(call_args[0]).name.casefold(), *call_args[1:2]]
        for call_args in command_calls
    ]


def _assert_windows_automount_tools_use_system32(
    command_calls: list[list[str]],
) -> None:
    assert command_calls
    for call_args in command_calls:
        tool_path = PureWindowsPath(call_args[0])
        assert tool_path.parent.name.casefold() == "system32"
        assert tool_path.name.casefold() in {"diskpart.exe", "mountvol.exe"}


@pytest.mark.skipif(os.name != "nt", reason="Windows automount lock is Windows-only")
def test_windows_automount_guard_lock_defaults_to_machine_wide_programdata(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARTHEXIS_WINDOWS_AUTOMOUNT_GUARD_LOCK", raising=False)
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")

    lock_path = PureWindowsPath(str(_windows_automount_guard_lock_path()))

    assert str(lock_path).casefold() == (
        r"c:\programdata\arthexis\locks\windows-automount-guard.lock"
    )


@pytest.mark.skipif(os.name != "nt", reason="msvcrt locking is Windows-only")
@patch("apps.imager.services.build_engine.time.sleep")
@patch("apps.imager.services.build_engine.msvcrt.locking")
def test_windows_automount_guard_lock_retries_until_available(
    locking_mock, sleep_mock
) -> None:
    lock_file = SimpleNamespace(fileno=lambda: 123)
    locking_mock.side_effect = [OSError("locked"), None]

    _lock_windows_automount_guard_file(lock_file)

    assert locking_mock.call_count == 2
    sleep_mock.assert_called_once()


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_refuses_protected_disk(
    list_devices_mock, tmp_path: Path
) -> None:
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
        write_image_to_device(
            device_path="/dev/sda", image_path=str(source), confirmed=True
        )


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_refuses_mounted_target(
    list_devices_mock, tmp_path: Path
) -> None:
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
        write_image_to_device(
            device_path="/dev/sdb", image_path=str(source), confirmed=True
        )


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_refuses_blocked_security_media(
    list_devices_mock, tmp_path: Path
) -> None:
    """Regression: known security-key media must be rejected even when unmounted."""

    source = tmp_path / "source.img"
    source.write_bytes(b"safe")
    target = tmp_path / "iamakey.bin"
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
            vendor="LaCie",
            model="iamaKey",
            serial="75754c214ff0f2",
            write_blocked_reason=(
                "LaCie iamaKey media is reserved for bastion USB unlock keys."
            ),
        )
    ]

    with pytest.raises(ImagerBuildError, match="blocked media"):
        write_image_to_device(
            device_path=str(target), image_path=str(source), confirmed=True
        )

    assert target.read_bytes() == b"\0" * 32


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
    progress_callback = Mock()

    result = write_image_to_device(
        device_path=str(target),
        artifact_name="stable",
        confirmed=True,
        progress_callback=progress_callback,
    )

    artifact.refresh_from_db()
    assert target.read_bytes()[: source.stat().st_size] == source.read_bytes()
    assert result.verified is True
    assert artifact.metadata["last_write"]["device_path"] == str(target)
    assert (
        progress_callback.mock_calls.count(
            call(source.stat().st_size, source.stat().st_size)
        )
        >= 3
    )


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


@pytest.mark.django_db
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_guards_windows_automount(
    list_devices_mock, run_mock, tmp_path: Path
) -> None:
    """Regression: Windows writes should disable automount and restore it afterward."""

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
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(command_calls, scripts)

    result = write_image_to_device(
        device_path=str(target),
        image_path=str(source),
        confirmed=True,
        windows_automount_guard=True,
    )

    assert result.verified is True
    _assert_windows_automount_tools_use_system32(command_calls)
    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
        ["diskpart.exe", "/s"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
        "automount enable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine._windows_automount_guard_lock")
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_serializes_windows_automount_guard(
    list_devices_mock, run_mock, lock_mock, tmp_path: Path
) -> None:
    """Regression: overlapping guarded writes must serialize host automount changes."""

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
    lock_events: list[str] = []

    @contextmanager
    def fake_lock():
        lock_events.append("enter")
        yield
        lock_events.append("exit")

    lock_mock.side_effect = fake_lock
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(command_calls, scripts)

    result = write_image_to_device(
        device_path=str(target),
        image_path=str(source),
        confirmed=True,
        windows_automount_guard=True,
    )

    assert result.verified is True
    assert lock_mock.call_count == 1
    assert lock_events == ["enter", "exit"]
    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
        ["diskpart.exe", "/s"],
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_rechecks_mountpoints_after_windows_automount_guard(
    list_devices_mock, run_mock, tmp_path: Path
) -> None:
    """Regression: refuse media that remounts before the guard is active."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"artifact-bytes")
    target = tmp_path / "device.bin"
    target.write_bytes(b"\0" * 32)
    initial_device = BlockDeviceInfo(
        path=str(target),
        size_bytes=32,
        transport="usb",
        removable=True,
        mountpoints=[],
        partitions=[],
        protected=False,
    )
    remounted_device = BlockDeviceInfo(
        path=str(target),
        size_bytes=32,
        transport="usb",
        removable=True,
        mountpoints=["E:\\"],
        partitions=[],
        protected=False,
    )
    list_devices_mock.side_effect = [[initial_device], [remounted_device]]
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(command_calls, scripts)

    with pytest.raises(ImagerBuildError, match="Unmount all partitions first: E:\\\\"):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            windows_automount_guard=True,
        )

    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
        ["diskpart.exe", "/s"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
        "automount enable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine._copy_image_to_device_with_speed_guard")
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_reports_windows_automount_restore_failure_after_write_failure(
    list_devices_mock, run_mock, copy_mock, tmp_path: Path
) -> None:
    """Regression: restore failures must be visible when a guarded write fails."""

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
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(
        command_calls,
        scripts,
        fail_restore=True,
    )
    copy_mock.side_effect = ImagerBuildError("simulated write failure")

    with pytest.raises(
        ImagerBuildError,
        match=(
            "simulated write failure.*also could not restore.*simulated restore failure"
        ),
    ):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            windows_automount_guard=True,
        )

    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine._copy_image_to_device_with_speed_guard")
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_restores_windows_automount_after_write_failure(
    list_devices_mock, run_mock, copy_mock, tmp_path: Path
) -> None:
    """Regression: failed Windows burns must not leave automount disabled."""

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
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(command_calls, scripts)
    copy_mock.side_effect = ImagerBuildError("simulated write failure")

    with pytest.raises(ImagerBuildError, match="simulated write failure"):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            windows_automount_guard=True,
        )

    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
        ["diskpart.exe", "/s"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
        "automount enable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine._copy_image_to_device_with_speed_guard")
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_restores_windows_automount_after_interrupt(
    list_devices_mock, run_mock, copy_mock, tmp_path: Path
) -> None:
    """Regression: interrupted Windows burns must not leave automount disabled."""

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
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(command_calls, scripts)
    copy_mock.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            windows_automount_guard=True,
        )

    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
        ["diskpart.exe", "/s"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
        "automount enable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine._copy_image_to_device_with_speed_guard")
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_reports_windows_automount_restore_failure_after_interrupt(
    list_devices_mock, run_mock, copy_mock, tmp_path: Path
) -> None:
    """Regression: interrupted writes should surface failed automount restores."""

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
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(
        command_calls,
        scripts,
        fail_restore=True,
    )
    copy_mock.side_effect = KeyboardInterrupt

    with pytest.raises(
        ImagerBuildError,
        match="interrupted by KeyboardInterrupt.*also could not restore.*simulated restore failure",
    ):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            windows_automount_guard=True,
        )

    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/E"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.build_engine.subprocess.run")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_preserves_disabled_windows_automount(
    list_devices_mock, run_mock, tmp_path: Path
) -> None:
    """Regression: guarded writes must not enable an already-disabled policy."""

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
    command_calls: list[list[str]] = []
    scripts: list[str] = []
    run_mock.side_effect = _record_windows_automount_commands(
        command_calls,
        scripts,
        automount_enabled=False,
    )

    result = write_image_to_device(
        device_path=str(target),
        image_path=str(source),
        confirmed=True,
        windows_automount_guard=True,
    )

    assert result.verified is True
    assert _windows_automount_command_summary(command_calls) == [
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
        ["mountvol.exe", "/N"],
        ["diskpart.exe", "/s"],
    ]
    assert scripts == [
        "automount\nexit\n",
        "automount disable\nexit\n",
        "automount disable\nexit\n",
    ]


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_aborts_when_initial_write_rate_is_too_slow(
    list_devices_mock, tmp_path: Path
) -> None:
    """Regression: slow burner connections should fail early with operator guidance."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"0123456789abcdef")
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

    with (
        patch(
            "apps.imager.services.build_engine.DEFAULT_IMAGE_WRITE_CHUNK_SIZE_BYTES", 4
        ),
        patch(
            "apps.imager.services.build_engine.time.monotonic",
            side_effect=[0.0, 40.0],
        ),
        pytest.raises(
            ImagerBuildError,
            match="burner may be improperly connected.*Connect the burner directly to USB",
        ),
    ):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            min_write_rate_bytes_per_second=1.0,
            write_rate_grace_seconds=30.0,
        )

    assert target.read_bytes()[:4] == b"0123"
    assert target.read_bytes()[4:16] == b"\0" * 12


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_allows_zero_elapsed_rate_sample(
    list_devices_mock, tmp_path: Path
) -> None:
    """Regression: zero elapsed samples should not look like slow writes."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"01234567")
    target = tmp_path / "device.bin"
    target.write_bytes(b"\0" * 16)
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path=str(target),
            size_bytes=16,
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=[],
            protected=False,
        )
    ]

    with (
        patch(
            "apps.imager.services.build_engine.DEFAULT_IMAGE_WRITE_CHUNK_SIZE_BYTES", 4
        ),
        patch(
            "apps.imager.services.build_engine.time.monotonic",
            side_effect=[10.0, 10.0],
        ),
    ):
        result = write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            min_write_rate_bytes_per_second=1.0,
            write_rate_grace_seconds=0.0,
        )

    assert target.read_bytes()[:8] == b"01234567"
    assert result.verified is True


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_backs_up_target_before_write(
    list_devices_mock, tmp_path: Path
) -> None:
    """Regression: --backup should capture and verify target media before burning."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"artifact-bytes")
    target = tmp_path / "device.bin"
    original_target = b"existing-media-state-before-burn"
    target.write_bytes(original_target)
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path=str(target),
            size_bytes=len(original_target),
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
    progress_callback = Mock()

    result = write_image_to_device(
        device_path=str(target),
        artifact_name="stable",
        confirmed=True,
        backup=True,
        backup_dir=tmp_path / "backups",
        progress_callback=progress_callback,
    )

    artifact.refresh_from_db()
    assert result.backup is not None
    assert result.backup.verified is True
    assert result.backup.path.parent == (tmp_path / "backups").resolve()
    assert result.backup.path.read_bytes() == original_target
    assert target.read_bytes()[: source.stat().st_size] == source.read_bytes()
    backup_metadata = artifact.metadata["last_write"]["backup"]
    assert backup_metadata["path"] == str(result.backup.path)
    assert backup_metadata["size_bytes"] == len(original_target)
    assert backup_metadata["sha256"] == result.backup.sha256
    assert backup_metadata["verified"] is True
    progress_callback.assert_has_calls(
        [
            call(len(original_target), len(original_target)),
            call(source.stat().st_size, source.stat().st_size),
        ],
        any_order=True,
    )


@pytest.mark.django_db
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_blocks_write_when_backup_cannot_be_created(
    list_devices_mock, tmp_path: Path
) -> None:
    """Regression: backup failures must prevent the destructive write."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"artifact-bytes")
    target = tmp_path / "device.bin"
    original_target = b"existing-media-state-before-burn"
    target.write_bytes(original_target)
    backup_dir = tmp_path / "backup-path-is-file"
    backup_dir.write_text("not a directory", encoding="utf-8")
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path=str(target),
            size_bytes=len(original_target),
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=[],
            protected=False,
        )
    ]

    with pytest.raises(ImagerBuildError, match="backup directory"):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            backup=True,
            backup_dir=backup_dir,
        )

    assert target.read_bytes() == original_target


@pytest.mark.django_db
@patch("apps.imager.services.build_engine.shutil.disk_usage")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_blocks_write_when_backup_space_is_insufficient(
    list_devices_mock, disk_usage_mock, tmp_path: Path
) -> None:
    """Regression: --backup should refuse when the destination lacks capacity."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"artifact-bytes")
    target = tmp_path / "device.bin"
    original_target = b"existing-media-state-before-burn"
    target.write_bytes(original_target)
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path=str(target),
            size_bytes=len(original_target),
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=[],
            protected=False,
        )
    ]
    disk_usage_mock.return_value = SimpleNamespace(free=len(original_target) - 1)

    with pytest.raises(ImagerBuildError, match="Insufficient free space"):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            backup=True,
            backup_dir=tmp_path / "backups",
        )

    assert target.read_bytes() == original_target


@pytest.mark.django_db
@patch("apps.imager.services.build_engine._sha256_for_file", return_value="mismatch")
@patch("apps.imager.services.list_block_devices")
def test_write_image_to_device_blocks_write_when_backup_verification_fails(
    list_devices_mock, _sha256_mock, tmp_path: Path
) -> None:
    """Regression: backup checksum failures must prevent the destructive write."""

    source = tmp_path / "artifact.img"
    source.write_bytes(b"artifact-bytes")
    target = tmp_path / "device.bin"
    original_target = b"existing-media-state-before-burn"
    target.write_bytes(original_target)
    list_devices_mock.return_value = [
        BlockDeviceInfo(
            path=str(target),
            size_bytes=len(original_target),
            transport="usb",
            removable=True,
            mountpoints=[],
            partitions=[],
            protected=False,
        )
    ]

    with pytest.raises(ImagerBuildError, match="backup verification failed"):
        write_image_to_device(
            device_path=str(target),
            image_path=str(source),
            confirmed=True,
            backup=True,
            backup_dir=tmp_path / "backups",
        )

    assert target.read_bytes() == original_target
