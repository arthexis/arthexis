"""Compatibility exports for imager services.

The implementation is split by responsibility across this package. Existing
callers can continue importing service symbols from ``apps.imager.services``.
"""

import os
import shutil
import socket
import subprocess
from urllib.request import build_opener, urlopen

from apps.imager.reservations import (  # noqa: E402
    commit_image_reservation,
    plan_image_reservation,
)

from . import build_engine as _build_engine_module  # noqa: E402
from . import guestfish as _guestfish_module  # noqa: E402
from . import source as _source_module  # noqa: E402
from .artifacts import (
    _build_download_uri,
    _build_local_http_url,
    _build_profile_manifest,
    _build_served_artifact_url,
    _coerce_profile_metadata,
    _format_url_host,
    _image_size_metadata,
    _network_profiles_metadata,
    _reservation_metadata,
    _sanitize_storage_options,
    _sha256_for_file,
    _sha256_for_prefix,
    _suite_bundle_metadata,
)
from .build_engine import (
    ARTHEXIS_BOOTSTRAP_PROFILE,
    BOOTSTRAP_SCRIPT,
    BOOTSTRAP_SYSTEMD_SERVICE_PATH,
    BOOTSTRAP_SYSTEMD_WANTS_PATH,
    BUILD_ENGINES,
    CONNECT_OTA_PROFILE,
    FIRST_RUN_SCRIPT,
    RECOVERY_ACCESS_SCRIPT,
    RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
    RECOVERY_BOOT_HOOK,
    RECOVERY_SSHD_CONFIG,
    RECOVERY_SSHD_CONFIG_REMOTE_PATH,
    RECOVERY_STALE_FILE_PATHS,
    RECOVERY_SYSTEMD_SERVICE,
    RECOVERY_SYSTEMD_SERVICE_PATH,
    RECOVERY_SYSTEMD_WANTS_PATH,
    SYSTEMD_MULTI_USER_WANTS_PATH,
    SYSTEMD_SERVICE,
    BuildEngine,
    _coerce_windows_access_paths,
    _coerce_windows_json_rows,
    _confirm_destructive_write,
    _create_suite_bundle,
    _customize_image,
    _http_access_check,
    _list_windows_block_devices,
    _normalize_recovery_ssh_access,
    _render_bootstrap_script,
    _resolve_image_path_for_write,
    _resolve_root_disk_path,
    _should_exclude_suite_bundle_path,
    _ssh_access_check,
    _tcp_access_check,
    _walk_block_descendants,
    _write_linux_text,
    build_rpi4b_image,
    list_block_devices,
    normalize_recovery_authorized_key_line,
    prepare_image_serve,
    serve_image_file,
    test_rpi_access,
    write_image_to_device,
)
from .guestfish import (
    _ensure_guestfish,
    _ensure_image_minimum_size,
    _expand_root_partition_to_image,
    _guestfish_mkdir_p,
    _guestfish_mkdir_p_command,
    _guestfish_remove_file,
    _guestfish_remove_file_command,
    _guestfish_run_commands,
    _guestfish_symlink,
    _guestfish_symlink_command,
    _guestfish_upload_commands,
    _guestfish_write,
    _normalize_minimum_image_size_bytes,
    _run_guestfish_command,
    _run_guestfish_raw_script,
    _run_guestfish_script,
)
from .models import (
    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES,
    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB,
    DEFAULT_HOST_NETWORK_PROFILE_DIR,
    DEFAULT_IMAGE_WRITE_BACKUP_DIR,
    DEFAULT_RECOVERY_SSH_USER,
    IMAGE_PARTITION_SECTOR_SIZE_BYTES,
    IMAGE_SIZE_BYTES_PER_GIB,
    NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH,
    RECOVERY_SSH_FORBIDDEN_USERS,
    RECOVERY_SSH_USERNAME_PATTERN,
    RPI_ROOT_DISK_DEVICE,
    RPI_ROOT_PARTITION_DEVICE,
    RPI_ROOT_PARTITION_NUMBER,
    STORAGE_BACKEND_AZURE_BLOB,
    STORAGE_BACKEND_GCS,
    STORAGE_BACKEND_LOCAL,
    STORAGE_BACKEND_S3,
    SUITE_BUNDLE_EXCLUDED_NAMES,
    SUITE_BUNDLE_EXCLUDED_TOP_LEVEL,
    SUITE_BUNDLE_REMOTE_PATH,
    SUPPORTED_STORAGE_BACKENDS,
    TARGET_RPI4B,
    VALID_PUBLIC_KEY_PATTERN,
    VALID_PUBLIC_KEY_PREFIXES,
    AccessCheckResult,
    BlockDeviceInfo,
    BuildEngineProfile,
    BuildResult,
    ImageCustomizationResult,
    ImagerBuildError,
    ImageSizeAdjustment,
    NetworkProfileInfo,
    RecoveryAuthorizedKeyError,
    RecoverySSHAccess,
    RpiAccessTestResult,
    ServeResult,
    SuiteBundleInfo,
    WriteBackupResult,
    WriteResult,
)
from .network_profiles import (
    _network_profile_remote_filename,
    _parse_network_profile_id,
    select_host_network_profiles,
)
from .source import (
    _copy_stream_to_file,
    _download_remote_base_image,
    _extract_base_image_archive,
    _is_disallowed_remote_host_address,
    _NoRedirectHandler,
    _normalize_local_source_path,
    _resolve_base_image,
    _validate_remote_base_image_url,
)

_build_rpi4b_image_impl = build_rpi4b_image
_write_image_to_device_impl = write_image_to_device
_customize_image_impl = _customize_image
_download_remote_base_image_impl = _download_remote_base_image
_ensure_image_minimum_size_impl = _ensure_image_minimum_size
_guestfish_remove_file_impl = _guestfish_remove_file
_guestfish_symlink_impl = _guestfish_symlink
_guestfish_write_impl = _guestfish_write
_test_rpi_access_impl = test_rpi_access
_validate_remote_base_image_url_impl = _validate_remote_base_image_url


def _sync_compatibility_globals() -> None:
    """Sync module-level compatibility function pointers for circular import resolution."""
    _build_engine_module._customize_image = globals()["_customize_image"]
    _build_engine_module._download_remote_base_image = globals()[
        "_download_remote_base_image"
    ]
    _build_engine_module._ensure_guestfish = globals()["_ensure_guestfish"]
    _build_engine_module._ensure_image_minimum_size = globals()[
        "_ensure_image_minimum_size"
    ]
    _build_engine_module._guestfish_run_commands = globals()["_guestfish_run_commands"]
    _build_engine_module._guestfish_write = globals()["_guestfish_write"]
    _build_engine_module._run_guestfish_raw_script = globals()[
        "_run_guestfish_raw_script"
    ]
    _build_engine_module._resolve_base_image = globals()["_resolve_base_image"]
    _build_engine_module.select_host_network_profiles = globals()[
        "select_host_network_profiles"
    ]
    _build_engine_module.list_block_devices = globals()["list_block_devices"]
    _build_engine_module.plan_image_reservation = globals()["plan_image_reservation"]
    _build_engine_module.socket = socket
    _build_engine_module.subprocess = subprocess
    _build_engine_module.urlopen = urlopen
    _guestfish_module._ensure_guestfish = globals()["_ensure_guestfish"]
    _guestfish_module._guestfish_run_commands = globals()["_guestfish_run_commands"]
    _guestfish_module._run_guestfish_raw_script = globals()["_run_guestfish_raw_script"]
    _guestfish_module._run_guestfish_script = globals()["_run_guestfish_script"]
    _source_module._download_remote_base_image = globals()[
        "_download_remote_base_image"
    ]
    _source_module._validate_remote_base_image_url = globals()[
        "_validate_remote_base_image_url"
    ]
    _source_module.build_opener = build_opener
    _source_module.socket = socket


def _customize_image(*args, **kwargs):
    """Customize a Raspberry Pi image by injecting scripts and configuration files."""
    _sync_compatibility_globals()
    return _customize_image_impl(*args, **kwargs)


def _download_remote_base_image(*args, **kwargs):
    """Download a remote base image while validating redirect targets."""
    _sync_compatibility_globals()
    return _download_remote_base_image_impl(*args, **kwargs)


def _ensure_image_minimum_size(*args, **kwargs):
    """Ensure image meets minimum size requirements by expanding if necessary."""
    _sync_compatibility_globals()
    return _ensure_image_minimum_size_impl(*args, **kwargs)


def _guestfish_remove_file(*args, **kwargs):
    """Remove a file from the image using guestfish."""
    _sync_compatibility_globals()
    return _guestfish_remove_file_impl(*args, **kwargs)


def _guestfish_symlink(*args, **kwargs):
    """Create a symbolic link in the image using guestfish."""
    _sync_compatibility_globals()
    return _guestfish_symlink_impl(*args, **kwargs)


def _guestfish_write(*args, **kwargs):
    """Write content to a file in the image using guestfish."""
    _sync_compatibility_globals()
    return _guestfish_write_impl(*args, **kwargs)


def _validate_remote_base_image_url(*args, **kwargs):
    """Validate remote image URL host policy prior to fetching."""
    _sync_compatibility_globals()
    return _validate_remote_base_image_url_impl(*args, **kwargs)


def build_rpi4b_image(*args, **kwargs):
    """Build a customized Raspberry Pi 4B image artifact."""
    _sync_compatibility_globals()
    return _build_rpi4b_image_impl(*args, **kwargs)


def test_rpi_access(*args, **kwargs):
    """Test SSH and HTTP access to a deployed Raspberry Pi."""
    _sync_compatibility_globals()
    return _test_rpi_access_impl(*args, **kwargs)


def write_image_to_device(*args, **kwargs):
    """Write an image artifact to a block device with safety checks and verification."""
    _sync_compatibility_globals()
    return _write_image_to_device_impl(*args, **kwargs)
