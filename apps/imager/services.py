"""Build services for Arthexis Raspberry Pi image artifacts."""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import lzma
import os
import re
import shlex
import shutil
import socket
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, quote, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.reservations import (
    ImageReservation,
    RESERVATION_ENV_PATH,
    RESERVATION_JSON_PATH,
    active_parent_network_names,
    commit_image_reservation,
    plan_image_reservation,
    render_reservation_env,
    render_reservation_json,
)

TARGET_RPI4B = "rpi-4b"
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

BOOTSTRAP_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

RESERVED_NODE_ENV=/usr/local/share/arthexis/reserved-node.env
if [ -f "$RESERVED_NODE_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$RESERVED_NODE_ENV"
  set +a
fi

if [ -n "${NODE_HOSTNAME:-}" ]; then
  hostnamectl set-hostname "$NODE_HOSTNAME" 2>/dev/null || hostname "$NODE_HOSTNAME" 2>/dev/null || true
fi

missing_packages=()
if ! command -v git >/dev/null 2>&1; then
  missing_packages+=(git ca-certificates)
elif [ ! -e /etc/ssl/certs/ca-certificates.crt ]; then
  missing_packages+=(ca-certificates)
fi

if [ "${#missing_packages[@]}" -gt 0 ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update || { sleep 10; apt-get update; }
  apt-get install -y --no-install-recommends "${missing_packages[@]}"
fi

APP_HOME=/opt/arthexis
ARTHEXIS_BUNDLE=/usr/local/share/arthexis/arthexis-suite.tar.gz
if [ ! -x "$APP_HOME/start.sh" ] && [ -f "$ARTHEXIS_BUNDLE" ]; then
  rm -rf "$APP_HOME"
  install -d -m 755 "$APP_HOME"
  tar -xzf "$ARTHEXIS_BUNDLE" -C "$APP_HOME"
fi

if [ ! -x "$APP_HOME/start.sh" ]; then
  git clone --depth 1 "${ARTHEXIS_GIT_URL}" "$APP_HOME"
fi

if [ -f "$RESERVED_NODE_ENV" ]; then
  touch "$APP_HOME/arthexis.env"
  chmod 600 "$APP_HOME/arthexis.env" || true
  while IFS= read -r line; do
    case "$line" in
      NODE_*=*)
        key="${line%%=*}"
        if ! grep -q "^${key}=" "$APP_HOME/arthexis.env"; then
          printf '%s\\n' "$line" >> "$APP_HOME/arthexis.env"
        fi
        ;;
    esac
  done < "$RESERVED_NODE_ENV"
fi

cd "$APP_HOME"
chmod +x ./install.sh ./env-refresh.sh ./start.sh ./manage.py 2>/dev/null || true
./env-refresh.sh --deps-only
./start.sh
"""

RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH = "/usr/local/share/arthexis/recovery_authorized_keys"
RECOVERY_SSHD_CONFIG_REMOTE_PATH = "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf"
BOOTSTRAP_SYSTEMD_SERVICE_PATH = "/etc/systemd/system/arthexis-bootstrap.service"
RECOVERY_SYSTEMD_SERVICE_PATH = "/etc/systemd/system/arthexis-recovery-access.service"
SYSTEMD_MULTI_USER_WANTS_PATH = "/etc/systemd/system/multi-user.target.wants"
BOOTSTRAP_SYSTEMD_WANTS_PATH = f"{SYSTEMD_MULTI_USER_WANTS_PATH}/arthexis-bootstrap.service"
RECOVERY_SYSTEMD_WANTS_PATH = f"{SYSTEMD_MULTI_USER_WANTS_PATH}/arthexis-recovery-access.service"
RECOVERY_STALE_FILE_PATHS = (
    RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
    "/usr/local/bin/arthexis-recovery-access.sh",
    RECOVERY_SSHD_CONFIG_REMOTE_PATH,
    RECOVERY_SYSTEMD_SERVICE_PATH,
    RECOVERY_SYSTEMD_WANTS_PATH,
    "/etc/sudoers.d/90-arthexis-recovery",
)

RECOVERY_ACCESS_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

RECOVERY_USER={ssh_user}
RECOVERY_HOME="/home/$RECOVERY_USER"

if ! id -u "$RECOVERY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups sudo "$RECOVERY_USER"
fi

usermod -aG sudo "$RECOVERY_USER" >/dev/null 2>&1 || true
echo "$RECOVERY_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-arthexis-recovery
chmod 0440 /etc/sudoers.d/90-arthexis-recovery

passwd -l "$RECOVERY_USER" >/dev/null 2>&1 || true
install -d -m 700 -o "$RECOVERY_USER" -g "$RECOVERY_USER" "$RECOVERY_HOME/.ssh"
install -m 600 -o "$RECOVERY_USER" -g "$RECOVERY_USER" {authorized_keys_path} "$RECOVERY_HOME/.ssh/authorized_keys"
systemctl enable ssh
"""

RECOVERY_SSHD_CONFIG = """PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
"""

RECOVERY_SYSTEMD_SERVICE = """[Unit]
Description=Arthexis recovery SSH access
DefaultDependencies=no
After=local-fs.target
Before=ssh.service sshd.service arthexis-bootstrap.service
Wants=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/arthexis-recovery-access.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

SYSTEMD_SERVICE = """[Unit]
Description=Arthexis first boot bootstrap
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=ARTHEXIS_GIT_URL={git_url}
ExecStart=/usr/local/bin/arthexis-bootstrap.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

FIRST_RUN_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

{recovery_boot_hook}

chmod +x /usr/local/bin/arthexis-bootstrap.sh
systemctl daemon-reload
systemctl enable arthexis-bootstrap.service
systemctl start arthexis-bootstrap.service
rm -f /boot/firstrun.sh /boot/firmware/firstrun.sh
"""

RECOVERY_BOOT_HOOK = """if [ -x /usr/local/bin/arthexis-recovery-access.sh ]; then
  /usr/local/bin/arthexis-recovery-access.sh || \\
    echo "arthexis-recovery-access.sh failed; continuing with bootstrap" >&2
fi"""


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
    reservation: dict[str, object] | None = None


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


@dataclass
class WriteResult:
    """Metadata returned from writing an image artifact to a block device."""

    device_path: str
    image_path: Path
    size_bytes: int
    source_sha256: str
    written_sha256: str
    verified: bool


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

        missing_fields = [field for field in self.required_manifest_fields if not manifest.get(field)]
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

        required_artifacts = profile_metadata.get("required_artifacts", self.required_artifacts)
        if not isinstance(required_artifacts, list | tuple):
            raise ImagerBuildError(f"Profile '{self.name}' requires required_artifacts as a list.")

        required_artifacts_set = {str(entry) for entry in required_artifacts if str(entry)}
        missing_artifacts = [name for name in self.required_artifacts if name not in required_artifacts_set]
        if missing_artifacts:
            raise ImagerBuildError(
                f"Profile '{self.name}' is missing required update-enablement artifacts: "
                + ", ".join(missing_artifacts)
                + "."
            )

        manifest = {
            "release_version": profile_metadata.get("release_version"),
            "compatibility_model": profile_metadata.get("compatibility_model"),
            "compatibility_board": profile_metadata.get("compatibility_board", default_board),
            "ota_channel": profile_metadata.get("ota_channel"),
            "ota_artifact_type": profile_metadata.get("ota_artifact_type", "raw-disk-image"),
            "required_artifacts": sorted(required_artifacts_set),
        }
        self.validate_manifest(manifest)
        return manifest


@dataclass(frozen=True)
class BuildEngine:
    """Build engine configuration that maps profile names to profile requirements."""

    name: str
    profiles: dict[str, BuildEngineProfile]

    def profile(self, profile_name: str) -> BuildEngineProfile:
        """Return a supported profile or raise a clear operator error."""

        if profile_name not in self.profiles:
            available_profiles = ", ".join(sorted(self.profiles))
            raise ImagerBuildError(
                f"Unsupported profile '{profile_name}' for engine '{self.name}'. Available profiles: {available_profiles}."
            )
        return self.profiles[profile_name]


CONNECT_OTA_PROFILE = BuildEngineProfile(
    name="connect-ota",
    required_base_os="raspberry-pi-os-trixie",
    required_architecture="arm64",
    required_artifacts=(
        "connect-ota-agent",
        "connect-ota-channel-config",
        "connect-ota-device-identity",
    ),
    required_manifest_fields=(
        "release_version",
        "compatibility_model",
        "compatibility_board",
        "ota_channel",
        "ota_artifact_type",
    ),
)

ARTHEXIS_BOOTSTRAP_PROFILE = BuildEngineProfile(
    name="bootstrap",
    required_base_os="",
    required_architecture="",
    required_artifacts=(),
    required_manifest_fields=(),
)

BUILD_ENGINES: dict[str, BuildEngine] = {
    "arthexis-bootstrap": BuildEngine(
        name="arthexis-bootstrap",
        profiles={
            "bootstrap": ARTHEXIS_BOOTSTRAP_PROFILE,
            "connect-ota": CONNECT_OTA_PROFILE,
        },
    ),
}


def _sha256_for_file(path: Path) -> str:
    """Compute the SHA-256 checksum for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_for_prefix(path: Path, *, size_bytes: int) -> str:
    """Compute SHA-256 for the first ``size_bytes`` bytes of a file/device."""

    digest = hashlib.sha256()
    remaining = size_bytes
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    if remaining != 0:
        raise ImagerBuildError(f"Could not read expected {size_bytes} bytes from {path}.")
    return digest.hexdigest()


def _ensure_guestfish() -> None:
    """Ensure guestfish is available for image customization."""

    if shutil.which("guestfish"):
        return
    raise ImagerBuildError(
        "guestfish is required to customize Raspberry Pi images. Install libguestfs-tools first."
    )


def normalize_recovery_authorized_key_line(line: str) -> str | None:
    """Normalize one recovery authorized-key line or raise a validation error."""

    normalized = line.strip()
    if not normalized or normalized.startswith("#"):
        return None
    if not VALID_PUBLIC_KEY_PATTERN.match(normalized):
        raise RecoveryAuthorizedKeyError("unrecognized key line")
    try:
        load_ssh_public_key(normalized.encode("utf-8"))
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise RecoveryAuthorizedKeyError("malformed public key line") from exc
    return normalized


def _should_exclude_suite_bundle_path(relative_path: Path) -> bool:
    """Return whether a repo path should be excluded from the static image bundle."""

    parts = relative_path.parts
    if not parts:
        return False
    if parts[0] in SUITE_BUNDLE_EXCLUDED_TOP_LEVEL:
        return True
    if any(part in SUITE_BUNDLE_EXCLUDED_NAMES for part in parts):
        return True
    name = relative_path.name
    return name == ".envrc" or name.startswith(".env.") or name.endswith((".env", ".pyc", ".pyo"))


def _create_suite_bundle(source_path: Path, archive_path: Path) -> SuiteBundleInfo:
    """Create a sanitized tarball of the suite source for image injection."""

    source = source_path.expanduser().resolve()
    if not source.is_dir():
        raise ImagerBuildError(f"Suite source path is not a directory: {source}")
    for required_file in ("manage.py", "start.sh", "env-refresh.sh"):
        if not (source / required_file).is_file():
            raise ImagerBuildError(f"Suite source path is missing required file: {required_file}")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    file_count = 0
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative_path = path.relative_to(source)
            if _should_exclude_suite_bundle_path(relative_path):
                continue
            archive.add(path, arcname=relative_path.as_posix(), recursive=False)
            file_count += 1
    if file_count == 0:
        raise ImagerBuildError(f"Suite source path did not contain any bundleable files: {source}")

    return SuiteBundleInfo(
        source_path=source,
        remote_path=SUITE_BUNDLE_REMOTE_PATH,
        sha256=_sha256_for_file(archive_path),
        size_bytes=archive_path.stat().st_size,
        file_count=file_count,
    )


def _parse_network_profile_id(profile_path: Path) -> str:
    """Read a NetworkManager connection id from a keyfile profile when present."""

    try:
        lines = profile_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return profile_path.stem

    in_connection_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_connection_section = line.lower() == "[connection]"
            continue
        if not in_connection_section or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == "id" and value.strip():
            return value.strip()
    return profile_path.stem


def _network_profile_remote_filename(source_path: Path) -> str:
    """Return a safe NetworkManager keyfile name for image injection."""

    filename = source_path.name
    if not filename.endswith(".nmconnection"):
        filename = f"{filename}.nmconnection"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", filename)


def select_host_network_profiles(
    *,
    profile_dir: Path | None = None,
    names: list[str] | tuple[str, ...] | None = None,
    copy_all: bool = False,
) -> tuple[NetworkProfileInfo, ...]:
    """Select host NetworkManager profiles for copying into the generated image."""

    requested_names = tuple(name.strip() for name in (names or ()) if str(name).strip())
    if not requested_names and not copy_all:
        return ()

    source_dir = (profile_dir or Path(DEFAULT_HOST_NETWORK_PROFILE_DIR)).expanduser().resolve()
    if not source_dir.is_dir():
        raise ImagerBuildError(f"Host NetworkManager profile directory does not exist: {source_dir}")

    candidates: list[tuple[Path, str, set[str]]] = []
    for path in sorted(source_dir.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve().relative_to(source_dir)
        except ValueError:
            continue
        profile_id = _parse_network_profile_id(path)
        candidates.append(
            (
                path,
                profile_id,
                {path.name, path.stem, profile_id},
            )
        )

    selected: list[tuple[Path, str]] = []
    if copy_all:
        selected.extend((path, profile_id) for path, profile_id, _aliases in candidates)

    for requested_name in requested_names:
        match = next(
            (
                (path, profile_id)
                for path, profile_id, aliases in candidates
                if requested_name in aliases
            ),
            None,
        )
        if match is None:
            available = ", ".join(sorted({alias for _, _, aliases in candidates for alias in aliases}))
            raise ImagerBuildError(
                f"Host network profile '{requested_name}' was not found. Available profiles: {available or '(none)'}."
            )
        if match not in selected:
            selected.append(match)

    used_filenames: set[str] = set()
    profiles: list[NetworkProfileInfo] = []
    for source_path, profile_id in selected:
        filename = _network_profile_remote_filename(source_path)
        if filename in used_filenames:
            stem = Path(filename).stem
            suffix = Path(filename).suffix or ".nmconnection"
            counter = 2
            while f"{stem}-{counter}{suffix}" in used_filenames:
                counter += 1
            filename = f"{stem}-{counter}{suffix}"
        used_filenames.add(filename)
        profiles.append(
            NetworkProfileInfo(
                name=profile_id,
                filename=filename,
                source_path=source_path,
                remote_path=f"{NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH}/{filename}",
            )
        )
    return tuple(profiles)


def _normalize_local_source_path(base_image_uri: str, parsed_uri: ParseResult) -> Path | None:
    """Normalize local filesystem path inputs across URI and platform-specific forms."""

    if (
        len(parsed_uri.scheme) == 1
        and parsed_uri.scheme.isalpha()
        and parsed_uri.path.startswith(("/", "\\"))
    ):
        return Path(f"{parsed_uri.scheme}:{unquote(parsed_uri.path)}")

    if parsed_uri.scheme == "":
        if re.match(r"^[A-Za-z]:[\\/]", base_image_uri):
            return Path(base_image_uri)
        return Path(base_image_uri)

    if parsed_uri.scheme != "file":
        return None

    decoded_path = unquote(parsed_uri.path)
    if parsed_uri.netloc and parsed_uri.netloc != "localhost":
        return Path(f"//{parsed_uri.netloc}{decoded_path}")
    if re.match(r"^/[A-Za-z]:/", decoded_path):
        return Path(decoded_path[1:])
    return Path(decoded_path)


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from auto-following redirects so redirect targets can be validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _download_remote_base_image(base_image_uri: str, destination: Path) -> None:
    """Download a remote base image while validating redirect targets."""

    opener = build_opener(_NoRedirectHandler())
    current_url = base_image_uri

    for _ in range(10):
        _validate_remote_base_image_url(current_url)

        request = Request(current_url)
        with opener.open(request) as response:
            status_code = response.getcode()
            if status_code in {301, 302, 303, 307, 308}:
                redirect_location = response.headers.get("Location")
                if not redirect_location:
                    raise ImagerBuildError(
                        f"Remote base image URL '{current_url}' returned a redirect without a Location header."
                    )
                current_url = urljoin(current_url, redirect_location)
                continue

            with destination.open("wb") as output_handle:
                shutil.copyfileobj(response, output_handle)
            return

    raise ImagerBuildError(f"Remote base image URL '{base_image_uri}' exceeded redirect limit.")


def _copy_stream_to_file(source_handle, destination: Path) -> Path:
    """Copy a binary stream into a destination file path."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output_handle:
        shutil.copyfileobj(source_handle, output_handle)
    return destination


def _extract_base_image_archive(source_path: Path, workspace: Path) -> Path:
    """Expand compressed base image formats into a local raw image path."""

    suffix = source_path.suffix.lower()
    if suffix not in {".xz", ".gz", ".zip"}:
        return source_path

    try:
        if suffix == ".zip":
            with zipfile.ZipFile(source_path) as archive:
                members = [member for member in archive.infolist() if not member.is_dir()]
                image_members = [
                    member for member in members if Path(member.filename).suffix.lower() == ".img"
                ]
                if len(image_members) == 1:
                    selected_member = image_members[0]
                elif len(members) == 1:
                    selected_member = members[0]
                else:
                    raise ImagerBuildError(
                        f"Base image archive '{source_path.name}' must contain exactly one image file."
                    )
                destination = workspace / Path(selected_member.filename).name
                with archive.open(selected_member) as input_handle:
                    return _copy_stream_to_file(input_handle, destination)

        destination = workspace / source_path.stem
        opener = lzma.open if suffix == ".xz" else gzip.open
        with opener(source_path, "rb") as input_handle:
            return _copy_stream_to_file(input_handle, destination)
    except (EOFError, gzip.BadGzipFile, lzma.LZMAError, zipfile.BadZipFile) as exc:
        raise ImagerBuildError(
            f"Base image archive '{source_path.name}' is invalid or corrupted: {exc}"
        ) from exc


def _resolve_base_image(base_image_uri: str, workspace: Path) -> Path:
    """Resolve local/file/http(s) base image inputs to a local filesystem path."""

    parsed = urlparse(base_image_uri)
    local_path = _normalize_local_source_path(base_image_uri, parsed)
    if local_path is not None:
        path = local_path.expanduser().resolve()
        if not path.exists():
            raise ImagerBuildError(f"Base image does not exist: {path}")
        return _extract_base_image_archive(path, workspace)

    if parsed.scheme not in {"http", "https"}:
        raise ImagerBuildError("Only file, http, and https base image URIs are supported.")

    destination_name = Path(unquote(parsed.path)).name or "base-image.img"
    destination = workspace / destination_name
    try:
        _download_remote_base_image(base_image_uri, destination)
    except (HTTPError, URLError) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise ImagerBuildError(f"Could not download base image: {reason}") from exc
    return _extract_base_image_archive(destination, workspace)


def _is_disallowed_remote_host_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return True when an IP address points to non-public network space."""

    return any(
        (
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_private,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _validate_remote_base_image_url(base_image_uri: str) -> None:
    """Validate remote image URL host policy prior to fetching."""

    parsed = urlparse(base_image_uri)
    host = parsed.hostname
    if not host:
        raise ImagerBuildError(
            "Base image URL is missing a host. Provide a public hostname or configure IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS."
        )

    allowed_hosts = set(getattr(settings, "IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS", ()))
    if allowed_hosts and host not in allowed_hosts:
        raise ImagerBuildError(
            f"Remote base image host '{host}' is not in IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS."
        )

    if allowed_hosts and host in allowed_hosts:
        return

    if not getattr(settings, "IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS", True):
        return

    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        host_ip = None

    if host_ip and _is_disallowed_remote_host_address(host_ip):
        raise ImagerBuildError(
            f"Remote base image host '{host}' resolves to a blocked non-public address. "
            "Adjust IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS or IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS only if this is intentional."
        )

    if host_ip:
        return

    try:
        addrinfos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return

    for family, _, _, _, sockaddr in addrinfos:
        if family == socket.AF_INET:
            address = ipaddress.ip_address(sockaddr[0])
        elif family == socket.AF_INET6:
            address = ipaddress.ip_address(sockaddr[0])
        else:
            continue
        if _is_disallowed_remote_host_address(address):
            raise ImagerBuildError(
                f"Remote base image host '{host}' resolves to blocked non-public address '{address}'. "
                "Adjust IMAGER_BLOCK_PRIVATE_REMOTE_IMAGE_HOSTS or IMAGER_ALLOWED_REMOTE_IMAGE_HOSTS only if this is intentional."
            )


def _run_guestfish_script(image_path: Path, script: str, *, error_message: str) -> None:
    """Run a guestfish script with image-local temporary and cache directories."""

    cache_dir = image_path.parent / ".libguestfs-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=image_path.parent) as temp_dir:
        guestfish_env = os.environ.copy()
        guestfish_env["TMPDIR"] = temp_dir
        guestfish_env["LIBGUESTFS_TMPDIR"] = temp_dir
        guestfish_env["LIBGUESTFS_CACHEDIR"] = str(cache_dir)
        result = subprocess.run(
            ["guestfish", "--rw", "-a", str(image_path), "-i"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
            env=guestfish_env,
        )
        if result.returncode != 0:
            raise ImagerBuildError(result.stderr.strip() or error_message)


def _guestfish_upload_commands(
    local_path: Path,
    remote_path: str,
    chmod_mode: str | None = None,
) -> list[str]:
    """Return guestfish commands for uploading a local file into the disk image."""

    script_parts = [
        f"upload {shlex.quote(str(local_path))} {shlex.quote(remote_path)}",
    ]
    if chmod_mode:
        script_parts.append(f"chmod {chmod_mode} {shlex.quote(remote_path)}")
    return script_parts


def _guestfish_mkdir_p_command(remote_path: str) -> str:
    return f"mkdir-p {shlex.quote(remote_path)}"


def _guestfish_remove_file_command(remote_path: str) -> str:
    return f"rm-f {shlex.quote(remote_path)}"


def _guestfish_symlink_command(*, target: str, link_path: str) -> str:
    return f"ln-sf {shlex.quote(target)} {shlex.quote(link_path)}"


def _guestfish_run_commands(
    image_path: Path,
    commands: list[str],
    *,
    error_message: str,
) -> None:
    script = "\n".join(commands) + "\n"
    _run_guestfish_script(image_path, script, error_message=error_message)


def _guestfish_write(
    image_path: Path,
    local_path: Path,
    remote_path: str,
    chmod_mode: str | None = None,
) -> None:
    """Upload a local file into the disk image using guestfish."""

    _guestfish_run_commands(
        image_path,
        _guestfish_upload_commands(local_path, remote_path, chmod_mode),
        error_message="guestfish failed while writing files",
    )


def _guestfish_mkdir_p(image_path: Path, remote_path: str) -> None:
    """Create a directory path in the disk image using guestfish."""

    _guestfish_run_commands(
        image_path,
        [_guestfish_mkdir_p_command(remote_path)],
        error_message="guestfish failed while creating directories",
    )


def _guestfish_remove_file(image_path: Path, remote_path: str) -> None:
    """Remove a file from the disk image using guestfish, ignoring missing paths."""

    _guestfish_run_commands(
        image_path,
        [_guestfish_remove_file_command(remote_path)],
        error_message="guestfish failed while removing files",
    )


def _guestfish_symlink(image_path: Path, *, target: str, link_path: str) -> None:
    """Create or replace a symlink inside the disk image using guestfish."""

    _guestfish_run_commands(
        image_path,
        [_guestfish_symlink_command(target=target, link_path=link_path)],
        error_message="guestfish failed while enabling systemd units",
    )


def _normalize_recovery_ssh_access(
    *,
    recovery_ssh_user: str,
    recovery_authorized_keys: list[str] | tuple[str, ...] | None,
) -> RecoverySSHAccess | None:
    """Normalize build input into an optional recovery SSH config."""

    normalized_keys = tuple(
        line.strip()
        for line in (recovery_authorized_keys or ())
        if str(line).strip()
    )

    supplied_username = (recovery_ssh_user or "").strip()
    username = supplied_username or DEFAULT_RECOVERY_SSH_USER
    if not RECOVERY_SSH_USERNAME_PATTERN.fullmatch(username):
        raise ImagerBuildError(f"Invalid recovery SSH username: '{username}'")
    if username in RECOVERY_SSH_FORBIDDEN_USERS:
        raise ImagerBuildError(f"Invalid recovery SSH username: '{username}'")
    if not normalized_keys:
        if supplied_username:
            raise ImagerBuildError(
                "Recovery SSH user was provided without recovery authorized keys. "
                "Provide --recovery-authorized-key-file or omit --recovery-ssh-user."
            )
        return None
    return RecoverySSHAccess(username=username, authorized_keys=normalized_keys)


def _customize_image(
    image_path: Path,
    *,
    git_url: str,
    recovery_ssh_access: RecoverySSHAccess | None = None,
    suite_source_path: Path | None = None,
    network_profiles: tuple[NetworkProfileInfo, ...] = (),
    reservation: ImageReservation | None = None,
) -> ImageCustomizationResult:
    """Inject bootstrap scripts and systemd units into the image."""

    _ensure_guestfish()
    with TemporaryDirectory(dir=image_path.parent) as temporary_directory:
        work_dir = Path(temporary_directory)
        bootstrap = work_dir / "arthexis-bootstrap.sh"
        service = work_dir / "arthexis-bootstrap.service"
        firstrun = work_dir / "firstrun.sh"
        recovery_service = work_dir / "arthexis-recovery-access.service"
        reservation_env = work_dir / "reserved-node.env"
        reservation_json = work_dir / "reserved-node.json"
        suite_bundle_info: SuiteBundleInfo | None = None

        bootstrap.write_text(BOOTSTRAP_SCRIPT, encoding="utf-8")
        service.write_text(SYSTEMD_SERVICE.format(git_url=git_url), encoding="utf-8")
        recovery_service.write_text(RECOVERY_SYSTEMD_SERVICE, encoding="utf-8")
        firstrun.write_text(
            FIRST_RUN_SCRIPT.format(
                recovery_boot_hook=RECOVERY_BOOT_HOOK
                if recovery_ssh_access and recovery_ssh_access.enabled
                else ""
            ),
            encoding="utf-8",
        )

        _guestfish_run_commands(
            image_path,
            [
                *_guestfish_upload_commands(
                    bootstrap,
                    "/usr/local/bin/arthexis-bootstrap.sh",
                    chmod_mode="0755",
                ),
                *_guestfish_upload_commands(service, BOOTSTRAP_SYSTEMD_SERVICE_PATH),
                _guestfish_mkdir_p_command(SYSTEMD_MULTI_USER_WANTS_PATH),
                _guestfish_symlink_command(
                    target=BOOTSTRAP_SYSTEMD_SERVICE_PATH,
                    link_path=BOOTSTRAP_SYSTEMD_WANTS_PATH,
                ),
            ],
            error_message="guestfish failed while injecting bootstrap files",
        )
        if reservation is not None:
            reservation_env.write_text(render_reservation_env(reservation), encoding="utf-8")
            reservation_json.write_text(render_reservation_json(reservation), encoding="utf-8")
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(str(PurePosixPath(RESERVATION_ENV_PATH).parent)),
                    *_guestfish_upload_commands(
                        reservation_env,
                        RESERVATION_ENV_PATH,
                        chmod_mode="0600",
                    ),
                    *_guestfish_upload_commands(
                        reservation_json,
                        RESERVATION_JSON_PATH,
                        chmod_mode="0644",
                    ),
                ],
                error_message="guestfish failed while injecting reserved node metadata",
            )
        if suite_source_path is not None:
            suite_bundle = work_dir / "arthexis-suite.tar.gz"
            suite_bundle_info = _create_suite_bundle(suite_source_path, suite_bundle)
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(str(PurePosixPath(SUITE_BUNDLE_REMOTE_PATH).parent)),
                    *_guestfish_upload_commands(
                        suite_bundle,
                        SUITE_BUNDLE_REMOTE_PATH,
                        chmod_mode="0644",
                    ),
                ],
                error_message="guestfish failed while injecting suite bundle",
            )
        if recovery_ssh_access and recovery_ssh_access.enabled:
            recovery_keys = work_dir / "recovery_authorized_keys"
            recovery_script = work_dir / "arthexis-recovery-access.sh"
            recovery_sshd_config = work_dir / "arthexis-recovery.conf"

            recovery_keys.write_text(
                "\n".join(recovery_ssh_access.authorized_keys) + "\n",
                encoding="utf-8",
            )
            recovery_script.write_text(
                RECOVERY_ACCESS_SCRIPT.format(
                    ssh_user=shlex.quote(recovery_ssh_access.username),
                    authorized_keys_path=RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
                ),
                encoding="utf-8",
            )
            recovery_sshd_config.write_text(RECOVERY_SSHD_CONFIG, encoding="utf-8")

            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        recovery_keys,
                        RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
                        chmod_mode="0600",
                    ),
                    *_guestfish_upload_commands(
                        recovery_script,
                        "/usr/local/bin/arthexis-recovery-access.sh",
                        chmod_mode="0755",
                    ),
                    *_guestfish_upload_commands(
                        recovery_sshd_config,
                        RECOVERY_SSHD_CONFIG_REMOTE_PATH,
                        chmod_mode="0644",
                    ),
                    *_guestfish_upload_commands(
                        recovery_service,
                        RECOVERY_SYSTEMD_SERVICE_PATH,
                        chmod_mode="0644",
                    ),
                    _guestfish_symlink_command(
                        target=RECOVERY_SYSTEMD_SERVICE_PATH,
                        link_path=RECOVERY_SYSTEMD_WANTS_PATH,
                    ),
                ],
                error_message="guestfish failed while injecting recovery files",
            )
        else:
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_remove_file_command(stale_file_path)
                    for stale_file_path in RECOVERY_STALE_FILE_PATHS
                ],
                error_message="guestfish failed while removing stale recovery files",
            )
        if network_profiles:
            profile_commands = [_guestfish_mkdir_p_command(NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH)]
            for profile in network_profiles:
                profile_commands.extend(
                    _guestfish_upload_commands(
                        profile.source_path,
                        profile.remote_path,
                        chmod_mode="0600",
                    )
                )
            _guestfish_run_commands(
                image_path,
                profile_commands,
                error_message="guestfish failed while injecting host network profiles",
            )
        try:
            _guestfish_write(image_path, firstrun, "/boot/firstrun.sh", chmod_mode="0755")
        except ImagerBuildError:
            _guestfish_write(image_path, firstrun, "/boot/firmware/firstrun.sh", chmod_mode="0755")

    return ImageCustomizationResult(
        suite_bundle=suite_bundle_info,
        network_profiles=network_profiles,
        reservation=reservation,
    )


def _coerce_profile_metadata(profile_metadata: dict[str, object] | None) -> dict[str, object]:
    """Normalize profile metadata into predictable keys for profile validation."""

    return dict(profile_metadata or {})


def _build_profile_manifest(
    *,
    build_profile: BuildEngineProfile,
    profile_metadata: dict[str, object],
) -> dict[str, object]:
    """Build rollout manifest metadata for a profile and validate mandatory fields."""
    return build_profile.build_manifest(
        profile_metadata=profile_metadata,
        default_board=TARGET_RPI4B,
    )


def _build_download_uri(download_base_uri: str, output_filename: str) -> str:
    """Build an optional hosted download URI for an artifact."""

    base = (download_base_uri or "").strip()
    if not base:
        return ""

    parsed_base = urlparse(base)
    if parsed_base.scheme not in {"http", "https"}:
        raise ImagerBuildError("Download base URI must use http or https.")
    if not parsed_base.hostname:
        raise ImagerBuildError("Download base URI must include a valid host.")

    normalized_path = f"{parsed_base.path.rstrip('/')}/{output_filename}"
    return parsed_base._replace(path=normalized_path).geturl()


def _suite_bundle_metadata(suite_bundle: SuiteBundleInfo | None) -> dict[str, object]:
    """Return JSON-safe metadata for static suite bundle injection."""

    if suite_bundle is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "source_path": str(suite_bundle.source_path),
        "remote_path": suite_bundle.remote_path,
        "sha256": suite_bundle.sha256,
        "size_bytes": suite_bundle.size_bytes,
        "file_count": suite_bundle.file_count,
    }


def _network_profiles_metadata(network_profiles: tuple[NetworkProfileInfo, ...]) -> dict[str, object]:
    """Return JSON-safe metadata for injected host network profiles."""

    return {
        "enabled": bool(network_profiles),
        "count": len(network_profiles),
        "profiles": [
            {
                "name": profile.name,
                "filename": profile.filename,
                "remote_path": profile.remote_path,
            }
            for profile in network_profiles
        ],
    }


def _reservation_metadata(reservation: dict[str, object] | None) -> dict[str, object]:
    """Return JSON-safe metadata for an image reservation."""

    if not reservation:
        return {"enabled": False}
    return {"enabled": True, **reservation}


def _format_url_host(host: str) -> str:
    """Bracket IPv6 hosts for URL construction."""

    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _build_served_artifact_url(
    *,
    output_filename: str,
    port: int,
    url_host: str = "",
    base_url: str = "",
) -> str:
    """Build the URL advertised for a locally served image artifact."""

    if base_url:
        parsed_base = urlparse(base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
            raise ImagerBuildError("Serve base URL must use http or https and include a host.")
        normalized_path = f"{parsed_base.path.rstrip('/')}/{quote(output_filename)}"
        return parsed_base._replace(path=normalized_path).geturl()

    advertised_host = _format_url_host((url_host or "127.0.0.1").strip())
    return f"http://{advertised_host}:{port}/{quote(output_filename)}"


def prepare_image_serve(
    *,
    artifact_name: str = "",
    image_path: str = "",
    host: str = "0.0.0.0",
    port: int = 8088,
    url_host: str = "",
    base_url: str = "",
    update_artifact_url: bool = True,
) -> ServeResult:
    """Resolve an image and optionally persist the URL used for local artifact serving."""

    resolved_path, artifact = _resolve_image_path_for_write(
        artifact_name=artifact_name,
        image_path=image_path,
    )
    artifact_url = _build_served_artifact_url(
        output_filename=resolved_path.name,
        port=port,
        url_host=url_host,
        base_url=base_url,
    )
    if artifact is not None and update_artifact_url:
        artifact.download_uri = artifact_url
        artifact.metadata = {
            **artifact.metadata,
            "local_serve": {
                "host": host,
                "port": port,
                "url": artifact_url,
                "updated_at": timezone.now().isoformat(),
            },
        }
        artifact.save(update_fields=["download_uri", "metadata", "updated_at"])
    return ServeResult(
        image_path=resolved_path,
        url=artifact_url,
        host=host,
        port=port,
    )


def serve_image_file(*, image_path: Path, host: str, port: int) -> None:
    """Serve a single image file over HTTP until interrupted."""

    image = image_path.resolve()
    filename = image.name

    class SingleImageHandler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802
            self._send_file(include_body=False)

        def do_GET(self) -> None:  # noqa: N802
            self._send_file(include_body=True)

        def _send_file(self, *, include_body: bool) -> None:
            requested_name = Path(unquote(urlparse(self.path).path).lstrip("/")).name
            if requested_name != filename:
                self.send_error(404, "Image artifact not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(image.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            if include_body:
                with image.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)

        def log_message(self, _format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer((host, port), SingleImageHandler) as server:
        server.serve_forever()


def _tcp_access_check(*, host: str, port: int, timeout: float, name: str) -> AccessCheckResult:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return AccessCheckResult(name=name, ok=True, detail=f"tcp/{port} reachable")
    except OSError as exc:
        return AccessCheckResult(name=name, ok=False, detail=f"tcp/{port} failed: {exc}")


def _ssh_access_check(
    *,
    host: str,
    user: str,
    port: int,
    key_path: str,
    timeout: float,
) -> AccessCheckResult:
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        return AccessCheckResult(name="ssh-auth", ok=False, detail="ssh command not found")

    command = [
        ssh_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={max(1, int(timeout))}",
        "-p",
        str(port),
    ]
    if key_path:
        command.extend(["-i", str(Path(key_path).expanduser())])
    command.extend([f"{user}@{host}", "true"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1, int(timeout) + 2),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AccessCheckResult(name="ssh-auth", ok=False, detail=f"ssh failed: {exc}")
    if result.returncode == 0:
        return AccessCheckResult(name="ssh-auth", ok=True, detail=f"{user}@{host}:{port} accepted key auth")
    detail = (result.stderr or result.stdout or "ssh command failed").strip().splitlines()
    return AccessCheckResult(name="ssh-auth", ok=False, detail=detail[-1] if detail else "ssh command failed")


def _http_access_check(*, url: str, timeout: float) -> AccessCheckResult:
    try:
        with urlopen(Request(url), timeout=timeout) as response:
            status = response.getcode()
    except (OSError, HTTPError, URLError) as exc:
        return AccessCheckResult(name="http", ok=False, detail=f"{url} failed: {exc}")
    return AccessCheckResult(
        name="http",
        ok=200 <= status < 500,
        detail=f"{url} returned HTTP {status}",
    )


def test_rpi_access(
    *,
    host: str,
    ssh_user: str = DEFAULT_RECOVERY_SSH_USER,
    ssh_port: int = 22,
    ssh_key: str = "",
    http_url: str = "",
    http_port: int = 8888,
    timeout: float = 5.0,
    skip_ssh: bool = False,
    skip_http: bool = False,
) -> RpiAccessTestResult:
    """Test SSH and HTTP access to a burned Raspberry Pi image."""

    checks: list[AccessCheckResult] = []
    if not skip_ssh:
        checks.append(_tcp_access_check(host=host, port=ssh_port, timeout=timeout, name="ssh-tcp"))
        checks.append(
            _ssh_access_check(
                host=host,
                user=ssh_user,
                port=ssh_port,
                key_path=ssh_key,
                timeout=timeout,
            )
        )
    if not skip_http:
        target_url = http_url or f"http://{_format_url_host(host)}:{http_port}/"
        checks.append(_http_access_check(url=target_url, timeout=timeout))
    if not checks:
        raise ImagerBuildError("Enable at least one access check.")
    return RpiAccessTestResult(host=host, checks=tuple(checks))


def _resolve_root_disk_path() -> str | None:
    """Resolve the current host root disk block path, if discoverable."""

    try:
        findmnt_result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            capture_output=True,
            text=True,
            check=False,
        )
        root_source = findmnt_result.stdout.strip()
        if findmnt_result.returncode != 0 or not root_source:
            return None

        current_path = root_source
        visited_paths: set[str] = set()
        while current_path and current_path not in visited_paths:
            visited_paths.add(current_path)
            info_result = subprocess.run(
                ["lsblk", "-n", "-o", "TYPE,PKNAME", current_path],
                capture_output=True,
                text=True,
                check=False,
            )
            if info_result.returncode != 0:
                return None
            info = info_result.stdout.strip().splitlines()
            if not info:
                return None
            parts = info[0].split(maxsplit=1)
            device_type = parts[0]
            parent_kernel_name = parts[1] if len(parts) > 1 else ""

            if device_type == "disk":
                return current_path
            if not parent_kernel_name:
                return None

            current_path = f"/dev/{parent_kernel_name}"
    except FileNotFoundError:
        return None
    return None


def _walk_block_descendants(entry: dict[str, object]) -> list[dict[str, object]]:
    """Return all descendants from an lsblk tree row."""

    descendants: list[dict[str, object]] = []
    for child in (entry.get("children") or []):
        if not isinstance(child, dict):
            continue
        descendants.append(child)
        descendants.extend(_walk_block_descendants(child))
    return descendants


def _coerce_windows_json_rows(value: object) -> list[dict[str, object]]:
    """Normalize PowerShell ConvertTo-Json array/singleton output."""

    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_windows_access_paths(value: object) -> list[str]:
    """Normalize Windows partition access paths from PowerShell JSON."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _list_windows_block_devices() -> list[BlockDeviceInfo]:
    """Enumerate Windows physical disks with safety metadata."""

    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        raise ImagerBuildError("PowerShell is required to enumerate Windows disks.")
    script = (
        "$ErrorActionPreference='Stop';"
        "$disks=@(Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType,Size,IsBoot,IsSystem,IsReadOnly,IsOffline,OperationalStatus);"
        "$partitions=@(Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,AccessPaths);"
        "[pscustomobject]@{disks=$disks;partitions=$partitions} | ConvertTo-Json -Depth 6 -Compress"
    )
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImagerBuildError("PowerShell is required to enumerate Windows disks.") from exc
    if result.returncode != 0:
        raise ImagerBuildError(result.stderr.strip() or "Unable to enumerate Windows disks.")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ImagerBuildError("Unable to parse Windows disk inventory output.") from exc

    partitions_by_disk: dict[int, list[dict[str, object]]] = {}
    for partition in _coerce_windows_json_rows(payload.get("partitions")):
        try:
            disk_number = int(partition.get("DiskNumber"))
        except (TypeError, ValueError):
            continue
        partitions_by_disk.setdefault(disk_number, []).append(partition)

    devices: list[BlockDeviceInfo] = []
    for disk in _coerce_windows_json_rows(payload.get("disks")):
        try:
            number = int(disk.get("Number"))
            size_bytes = int(disk.get("Size") or 0)
        except (TypeError, ValueError):
            continue

        bus_type = str(disk.get("BusType") or "")
        disk_partitions = partitions_by_disk.get(number, [])
        mountpoints: list[str] = []
        partitions: list[str] = []
        for partition in disk_partitions:
            partition_number = partition.get("PartitionNumber")
            if partition_number not in (None, ""):
                partitions.append(f"PhysicalDrive{number}Partition{partition_number}")
            drive_letter = str(partition.get("DriveLetter") or "").strip()
            if drive_letter:
                mountpoints.append(f"{drive_letter.upper()}:\\")
            mountpoints.extend(_coerce_windows_access_paths(partition.get("AccessPaths")))

        devices.append(
            BlockDeviceInfo(
                path=f"\\\\.\\PhysicalDrive{number}",
                size_bytes=size_bytes,
                transport=bus_type,
                removable=bus_type.lower() in {"usb", "sd", "mmc"},
                mountpoints=sorted(set(mountpoints)),
                partitions=partitions,
                protected=bool(disk.get("IsBoot") or disk.get("IsSystem")),
            )
        )
    return sorted(devices, key=lambda item: item.path)


def list_block_devices() -> list[BlockDeviceInfo]:
    """Enumerate host block devices and safety-relevant metadata."""

    if os.name == "nt":
        return _list_windows_block_devices()

    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "--tree", "-o", "PATH,SIZE,RM,TRAN,TYPE,MOUNTPOINTS"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImagerBuildError("The 'lsblk' command is required but was not found.") from exc
    if result.returncode != 0:
        raise ImagerBuildError(result.stderr.strip() or "Unable to enumerate block devices.")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ImagerBuildError("Unable to parse lsblk output for device discovery.") from exc

    root_disk = _resolve_root_disk_path()
    devices: list[BlockDeviceInfo] = []
    for entry in payload.get("blockdevices", []):
        if entry.get("type") != "disk":
            continue
        descendants = _walk_block_descendants(entry)
        mountpoints = [mount for mount in (entry.get("mountpoints") or []) if mount]
        mountpoints.extend(
            mount
            for child in descendants
            for mount in (child.get("mountpoints") or [])
            if mount
        )
        normalized_mountpoints = sorted(set(mountpoints))
        partitions = [child.get("path", "") for child in descendants if child.get("path")]
        devices.append(
            BlockDeviceInfo(
                path=str(entry.get("path", "")),
                size_bytes=int(entry.get("size") or 0),
                transport=str(entry.get("tran") or ""),
                removable=bool(entry.get("rm")),
                mountpoints=normalized_mountpoints,
                partitions=partitions,
                protected=str(entry.get("path", "")) == root_disk or "/" in normalized_mountpoints,
            )
        )
    return sorted(devices, key=lambda item: item.path)


def _resolve_image_path_for_write(*, artifact_name: str, image_path: str) -> tuple[Path, RaspberryPiImageArtifact | None]:
    """Resolve CLI write source from artifact registry or explicit image path."""

    if artifact_name:
        artifact = RaspberryPiImageArtifact.objects.filter(name=artifact_name).first()
        if artifact is None:
            raise ImagerBuildError(f"Artifact '{artifact_name}' does not exist.")
        resolved_path = Path(artifact.output_path).expanduser().resolve()
        if not resolved_path.exists():
            raise ImagerBuildError(f"Artifact image file does not exist: {resolved_path}")
        return resolved_path, artifact
    resolved_path = Path(image_path).expanduser().resolve()
    if not resolved_path.exists():
        raise ImagerBuildError(f"Image file does not exist: {resolved_path}")
    return resolved_path, None


def _confirm_destructive_write(*, device_path: str, image_path: Path, size_bytes: int, confirmed: bool) -> None:
    """Require explicit operator confirmation before device overwrite."""

    if confirmed:
        return
    raise ImagerBuildError(
        "Refusing write without explicit confirmation. Re-run with --yes.\n"
        f"Planned overwrite target: {device_path}\n"
        f"Source image: {image_path}\n"
        f"Bytes to write: {size_bytes}"
    )


def write_image_to_device(
    *,
    device_path: str,
    artifact_name: str = "",
    image_path: str = "",
    confirmed: bool = False,
) -> WriteResult:
    """Write an artifact/local image to a block device with safety checks and verification."""

    if bool(artifact_name) == bool(image_path):
        raise ImagerBuildError("Provide exactly one of artifact_name or image_path.")

    source_path, artifact = _resolve_image_path_for_write(
        artifact_name=artifact_name,
        image_path=image_path,
    )
    source_size = source_path.stat().st_size
    devices = {device.path: device for device in list_block_devices()}
    if device_path not in devices:
        raise ImagerBuildError(f"Target device '{device_path}' was not found in discovered block devices.")
    target_device = devices[device_path]

    if target_device.protected:
        raise ImagerBuildError(f"Refusing to overwrite protected system/root disk: {device_path}")
    if target_device.mountpoints:
        mounts = ", ".join(target_device.mountpoints)
        raise ImagerBuildError(
            f"Refusing to overwrite mounted device '{device_path}'. Unmount all partitions first: {mounts}"
        )
    if target_device.size_bytes < source_size:
        raise ImagerBuildError(
            f"Target device '{device_path}' is too small ({target_device.size_bytes} bytes) for image size {source_size} bytes."
        )

    _confirm_destructive_write(
        device_path=device_path,
        image_path=source_path,
        size_bytes=source_size,
        confirmed=confirmed,
    )

    source_hash = _sha256_for_file(source_path)
    with source_path.open("rb") as source_handle, open(device_path, "r+b", buffering=0) as device_handle:
        shutil.copyfileobj(source_handle, device_handle, length=1024 * 1024 * 4)
        device_handle.flush()
        os.fsync(device_handle.fileno())
    write_hash = _sha256_for_prefix(Path(device_path), size_bytes=source_size)
    verified = source_hash == write_hash
    if not verified:
        raise ImagerBuildError(f"Verification failed for '{device_path}': checksum mismatch after write.")

    if artifact is not None:
        artifact.metadata = {
            **artifact.metadata,
            "last_write": {
                "device_path": device_path,
                "source_path": str(source_path),
                "size_bytes": source_size,
                "sha256": source_hash,
                "verified": True,
                "verified_at": timezone.now().isoformat(),
            },
        }
        artifact.save(update_fields=["metadata", "updated_at"])

    return WriteResult(
        device_path=device_path,
        image_path=source_path,
        size_bytes=source_size,
        source_sha256=source_hash,
        written_sha256=write_hash,
        verified=verified,
    )


def build_rpi4b_image(
    *,
    name: str,
    base_image_uri: str,
    output_dir: Path,
    download_base_uri: str,
    git_url: str,
    customize: bool = True,
    build_engine: str = "arthexis-bootstrap",
    profile: str = "bootstrap",
    profile_metadata: dict[str, object] | None = None,
    recovery_ssh_user: str = "",
    recovery_authorized_keys: list[str] | tuple[str, ...] | None = None,
    skip_recovery_ssh: bool = False,
    bundle_suite: bool = True,
    suite_source_path: Path | None = None,
    copy_all_host_networks: bool = False,
    host_network_names: list[str] | tuple[str, ...] | None = None,
    host_network_profile_dir: Path | None = None,
    copy_parent_networks: bool = False,
    reserve_node: bool = False,
    reserve_hostname_prefix: str = "",
    reserve_number: int | None = None,
    reserve_role: str = "",
) -> BuildResult:
    """Build and register a Raspberry Pi 4B Arthexis image artifact."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ImagerBuildError(
            "Artifact name must start with an alphanumeric character and use only letters, numbers, dot, underscore, or hyphen."
        )

    engine = BUILD_ENGINES.get(build_engine)
    if engine is None:
        available_engines = ", ".join(sorted(BUILD_ENGINES))
        raise ImagerBuildError(
            f"Unsupported build engine '{build_engine}'. Available engines: {available_engines}."
        )
    selected_profile = engine.profile(profile)
    normalized_profile_metadata = _coerce_profile_metadata(profile_metadata)
    profile_manifest = _build_profile_manifest(
        build_profile=selected_profile,
        profile_metadata=normalized_profile_metadata,
    )
    recovery_ssh_access = _normalize_recovery_ssh_access(
        recovery_ssh_user=recovery_ssh_user,
        recovery_authorized_keys=recovery_authorized_keys,
    )
    if skip_recovery_ssh and recovery_ssh_access and recovery_ssh_access.enabled:
        raise ImagerBuildError("skip_recovery_ssh cannot be combined with recovery SSH keys.")
    if customize and not skip_recovery_ssh and not (recovery_ssh_access and recovery_ssh_access.enabled):
        raise ImagerBuildError(
            "Recovery SSH is required for customized image builds. "
            "Provide recovery authorized keys or explicitly skip recovery SSH."
        )
    if recovery_ssh_access and recovery_ssh_access.enabled and not customize:
        raise ImagerBuildError(
            "Recovery SSH access requires image customization. Remove --skip-customize or omit recovery key options."
        )
    if not customize:
        bundle_suite = False
        if reserve_node:
            raise ImagerBuildError(
                "Reserved node images require image customization. Remove --skip-customize or omit --reserve."
            )
        if copy_all_host_networks or host_network_names or copy_parent_networks:
            raise ImagerBuildError("Host network profile copying requires image customization.")

    resolved_suite_source_path = suite_source_path
    if customize and bundle_suite and resolved_suite_source_path is None:
        resolved_suite_source_path = Path(settings.BASE_DIR)
    resolved_host_network_names = list(host_network_names or ())
    if copy_parent_networks and not copy_all_host_networks:
        for profile_name in active_parent_network_names():
            if profile_name not in resolved_host_network_names:
                resolved_host_network_names.append(profile_name)
    network_profiles = select_host_network_profiles(
        profile_dir=host_network_profile_dir,
        names=resolved_host_network_names,
        copy_all=copy_all_host_networks,
    )
    try:
        reservation = (
            plan_image_reservation(
                hostname_prefix=reserve_hostname_prefix,
                number=reserve_number,
                role_name=reserve_role,
            )
            if reserve_node
            else None
        )
    except ValueError as exc:
        raise ImagerBuildError(str(exc)) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    output_filename = f"{name}-{TARGET_RPI4B}.img"
    output_path = output_dir / output_filename
    with TemporaryDirectory(dir=output_dir) as temporary_directory:
        source_path = _resolve_base_image(base_image_uri, Path(temporary_directory))
        if source_path.resolve() == output_path.resolve():
            raise ImagerBuildError("Base image path must differ from output artifact path.")
        shutil.copyfile(source_path, output_path)

    customization_result = ImageCustomizationResult()
    if customize:
        raw_customization_result = _customize_image(
            output_path,
            git_url=git_url,
            recovery_ssh_access=recovery_ssh_access,
            suite_source_path=resolved_suite_source_path if bundle_suite else None,
            network_profiles=network_profiles,
            reservation=reservation,
        )
        if isinstance(raw_customization_result, ImageCustomizationResult):
            customization_result = raw_customization_result

    sha256 = _sha256_for_file(output_path)
    size_bytes = output_path.stat().st_size
    download_uri = _build_download_uri(download_base_uri, output_filename)

    with transaction.atomic():
        try:
            reservation_commit = (
                commit_image_reservation(reservation)
                if reservation is not None
                else None
            )
        except ValueError as exc:
            raise ImagerBuildError(str(exc)) from exc
        reservation_payload = (
            reservation_commit.metadata()
            if reservation_commit is not None
            else None
        )
        RaspberryPiImageArtifact.objects.update_or_create(
            name=name,
            defaults={
                "target": TARGET_RPI4B,
                "base_image_uri": base_image_uri,
                "output_filename": output_filename,
                "output_path": str(output_path),
                "sha256": sha256,
                "size_bytes": size_bytes,
                "download_uri": download_uri,
                "metadata": {
                    "build_engine": build_engine,
                    "build_profile": profile,
                    "profile_manifest": profile_manifest,
                    "bootstrap_service": "arthexis-bootstrap.service",
                    "bootstrap_script": "/usr/local/bin/arthexis-bootstrap.sh",
                    "first_boot_script": "firstrun.sh",
                    "git_url": git_url,
                    "suite_bundle": _suite_bundle_metadata(customization_result.suite_bundle),
                    "host_network_profiles": _network_profiles_metadata(
                        customization_result.network_profiles
                    ),
                    "reserved_node": _reservation_metadata(reservation_payload),
                    "recovery_ssh": {
                        "enabled": bool(customize and recovery_ssh_access and recovery_ssh_access.enabled),
                        "user": recovery_ssh_access.username if recovery_ssh_access else "",
                        "authorized_key_count": len(recovery_ssh_access.authorized_keys)
                        if recovery_ssh_access
                        else 0,
                        "explicitly_skipped": bool(customize and skip_recovery_ssh),
                    },
                },
                "build_engine": build_engine,
                "build_profile": profile,
            },
        )

    return BuildResult(
        name=name,
        target=TARGET_RPI4B,
        base_image_uri=base_image_uri,
        output_path=output_path,
        sha256=sha256,
        size_bytes=size_bytes,
        download_uri=download_uri,
        build_engine=build_engine,
        build_profile=profile,
        profile_manifest=profile_manifest,
        reservation=reservation_payload,
    )
