"""Shared imager service data models and errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from apps.imager.reservations import ImageReservation

TARGET_RPI4B = "rpi-4b"
IMAGE_SIZE_BYTES_PER_GIB = 1024**3
IMAGE_PARTITION_SECTOR_SIZE_BYTES = 512
DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB = 8
DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES = (
    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_GIB * IMAGE_SIZE_BYTES_PER_GIB
)
RPI_ROOT_DISK_DEVICE = "/dev/sda"
RPI_ROOT_PARTITION_NUMBER = 2
RPI_ROOT_PARTITION_DEVICE = f"{RPI_ROOT_DISK_DEVICE}{RPI_ROOT_PARTITION_NUMBER}"
STORAGE_BACKEND_LOCAL = "local"
STORAGE_BACKEND_S3 = "s3"
STORAGE_BACKEND_GCS = "gcs"
STORAGE_BACKEND_AZURE_BLOB = "azure_blob"
SUPPORTED_STORAGE_BACKENDS = frozenset(
    {
        STORAGE_BACKEND_LOCAL,
        STORAGE_BACKEND_S3,
        STORAGE_BACKEND_GCS,
        STORAGE_BACKEND_AZURE_BLOB,
    }
)
DEFAULT_RECOVERY_SSH_USER = "arthe"
RECOVERY_SSH_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")
RECOVERY_SSH_FORBIDDEN_USERS = frozenset({"root"})
VALID_PUBLIC_KEY_PREFIXES = (
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ecdsa-sha2-nistp256@openssh.com",
    "sk-ssh-ed25519@openssh.com",
    "ssh-ed25519",
    "ssh-rsa",
)
VALID_PUBLIC_KEY_PATTERN = re.compile(
    r"^(?:"
    + "|".join(re.escape(prefix) for prefix in VALID_PUBLIC_KEY_PREFIXES)
    + r")\s+[A-Za-z0-9+/=]+(?:\s+.+)?$"
)
SUITE_BUNDLE_REMOTE_PATH = "/usr/local/share/arthexis/arthexis-suite.tar.gz"
NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH = "/etc/NetworkManager/system-connections"
DEFAULT_HOST_NETWORK_PROFILE_DIR = "/etc/NetworkManager/system-connections"
DEFAULT_IMAGE_WRITE_BACKUP_DIR = "build/rpi-imager/backups"
DEFAULT_IMAGE_WRITE_CHUNK_SIZE_BYTES = 1024 * 1024 * 4
DEFAULT_IMAGE_WRITE_MIN_RATE_BYTES_PER_SECOND = 1024 * 1024 * 2
DEFAULT_IMAGE_WRITE_SPEED_GRACE_SECONDS = 30.0
SUITE_BUNDLE_EXCLUDED_TOP_LEVEL = frozenset(
    {
        ".cache",
        ".git",
        ".locks",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "backups",
        "build",
        "cache",
        "env",
        "logs",
        "locks",
        "media",
        "node_modules",
        "staticfiles",
        "venv",
        "work",
    }
)
SUITE_BUNDLE_EXCLUDED_NAMES = frozenset(
    {
        ".env",
        ".envrc",
        "__pycache__",
        "db.sqlite3",
        "test_db.sqlite3",
    }
)


class ImagerBuildError(RuntimeError):
    """Raised when a Raspberry Pi image build cannot complete."""


class RecoveryAuthorizedKeyError(ValueError):
    """Raised when a recovery authorized-key line is malformed."""


@dataclass
class BuildResult:
    """Metadata returned from an image build operation."""

    name: str
    target: str
    base_image_uri: str
    output_path: Path
    sha256: str
    size_bytes: int
    download_uri: str
    build_engine: str
    build_profile: str
    profile_manifest: dict[str, object]
    storage_backend: str
    storage_options: dict[str, object]
    reservation: dict[str, object] | None = None


@dataclass(frozen=True)
class ImageSizeAdjustment:
    """Sizing actions applied to the raw disk image before customization."""

    requested_size_bytes: int
    original_size_bytes: int
    final_size_bytes: int
    image_extended: bool
    root_partition_expanded: bool
    root_partition_device: str = RPI_ROOT_PARTITION_DEVICE


@dataclass
class BlockDeviceInfo:
    """Block device information used for operator-safe write decisions."""

    path: str
    size_bytes: int
    transport: str
    removable: bool
    mountpoints: list[str]
    partitions: list[str]
    protected: bool
    vendor: str = ""
    model: str = ""
    serial: str = ""
    identity_paths: list[str] = field(default_factory=list)
    write_blocked_reason: str = ""


@dataclass
class WriteBackupResult:
    """Metadata returned from backing up target media before an image write."""

    path: Path
    size_bytes: int
    sha256: str
    verified: bool


@dataclass
class WriteResult:
    """Metadata returned from writing an image artifact to a block device."""

    device_path: str
    image_path: Path
    size_bytes: int
    source_sha256: str
    written_sha256: str
    verified: bool
    backup: WriteBackupResult | None = None


@dataclass(frozen=True)
class SuiteBundleInfo:
    """Metadata for a static Arthexis source bundle injected into an image."""

    source_path: Path
    remote_path: str
    sha256: str
    size_bytes: int
    file_count: int


@dataclass(frozen=True)
class NetworkProfileInfo:
    """NetworkManager profile selected for copying into a generated image."""

    name: str
    filename: str
    source_path: Path
    remote_path: str


@dataclass(frozen=True)
class ImageCustomizationResult:
    """Metadata produced while injecting first-boot customization files."""

    suite_bundle: SuiteBundleInfo | None = None
    network_profiles: tuple[NetworkProfileInfo, ...] = ()
    reservation: ImageReservation | None = None
    recovery_ap_psk_path: str = ""


@dataclass(frozen=True)
class ServeResult:
    """Metadata for a locally served image artifact."""

    image_path: Path
    url: str
    host: str
    port: int


@dataclass(frozen=True)
class AccessCheckResult:
    """Single RPi access check result."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class RpiAccessTestResult:
    """Aggregate access-test result for a burned Raspberry Pi image."""

    host: str
    checks: tuple[AccessCheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


@dataclass(frozen=True)
class RecoverySSHAccess:
    """Recovery SSH access configuration baked into an image artifact."""

    username: str
    authorized_keys: tuple[str, ...]

    @property
    def enabled(self) -> bool:
        """Return True when recovery SSH provisioning should be injected."""

        return bool(self.username and self.authorized_keys)


@dataclass(frozen=True)
class BuildEngineProfile:
    """Build engine profile contract for image validation and metadata generation."""

    name: str
    required_base_os: str
    required_architecture: str
    required_artifacts: tuple[str, ...]
    required_manifest_fields: tuple[str, ...]

    def builds_manifest(self) -> bool:
        """Return whether the profile requires rollout manifest generation."""

        return bool(
            self.required_base_os
            or self.required_architecture
            or self.required_artifacts
            or self.required_manifest_fields
        )

    def validate_base_requirements(self, metadata: dict[str, object]) -> None:
        """Validate source metadata against profile base OS and architecture requirements."""

        base_os = str(metadata.get("base_os", ""))
        architecture = str(metadata.get("architecture", ""))
        if base_os != self.required_base_os:
            raise ImagerBuildError(
                f"Profile '{self.name}' requires base_os={self.required_base_os}, got '{base_os or '(missing)'}'."
            )
        if architecture != self.required_architecture:
            raise ImagerBuildError(
                f"Profile '{self.name}' requires architecture={self.required_architecture}, got '{architecture or '(missing)'}'."
            )

    def validate_manifest(self, manifest: dict[str, object]) -> None:
        """Validate required profile-specific rollout and compatibility fields."""

        missing_fields = [
            field for field in self.required_manifest_fields if not manifest.get(field)
        ]
        if missing_fields:
            fields = ", ".join(missing_fields)
            raise ImagerBuildError(
                f"Profile '{self.name}' requires manifest fields: {fields}."
            )

    def build_manifest(
        self,
        *,
        profile_metadata: dict[str, object],
        default_board: str,
    ) -> dict[str, object]:
        """Build and validate the rollout manifest for this profile."""

        if not self.builds_manifest():
            return {}

        base_requirements = {
            "base_os": profile_metadata.get("base_os"),
            "architecture": profile_metadata.get("architecture"),
        }
        self.validate_base_requirements(base_requirements)

        required_artifacts = profile_metadata.get(
            "required_artifacts", self.required_artifacts
        )
        if not isinstance(required_artifacts, list | tuple):
            raise ImagerBuildError(
                f"Profile '{self.name}' requires required_artifacts as a list."
            )

        required_artifacts_set = {
            str(entry) for entry in required_artifacts if str(entry)
        }
        missing_artifacts = [
            name
            for name in self.required_artifacts
            if name not in required_artifacts_set
        ]
        if missing_artifacts:
            raise ImagerBuildError(
                f"Profile '{self.name}' is missing required update-enablement artifacts: "
                + ", ".join(missing_artifacts)
                + "."
            )

        manifest = {
            "release_version": profile_metadata.get("release_version"),
            "compatibility_model": profile_metadata.get("compatibility_model"),
            "compatibility_board": profile_metadata.get(
                "compatibility_board", default_board
            ),
            "ota_channel": profile_metadata.get("ota_channel"),
            "ota_artifact_type": profile_metadata.get(
                "ota_artifact_type", "raw-disk-image"
            ),
            "required_artifacts": sorted(required_artifacts_set),
        }
        self.validate_manifest(manifest)
        return manifest
