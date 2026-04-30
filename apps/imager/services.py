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
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import ParseResult, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.imager.models import RaspberryPiImageArtifact

TARGET_RPI4B = "rpi-4b"
DEFAULT_RECOVERY_SSH_USER = "arthe"
RECOVERY_SSH_USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$")
RECOVERY_SSH_FORBIDDEN_USERS = frozenset({"root"})

BOOTSTRAP_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

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
if [ ! -d "$APP_HOME/.git" ]; then
  git clone --depth 1 "${ARTHEXIS_GIT_URL}" "$APP_HOME"
fi

cd "$APP_HOME"
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
) -> None:
    """Inject bootstrap scripts and systemd units into the image."""

    _ensure_guestfish()
    with TemporaryDirectory(dir=image_path.parent) as temporary_directory:
        work_dir = Path(temporary_directory)
        bootstrap = work_dir / "arthexis-bootstrap.sh"
        service = work_dir / "arthexis-bootstrap.service"
        firstrun = work_dir / "firstrun.sh"
        recovery_service = work_dir / "arthexis-recovery-access.service"

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
                        str(Path(RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH).parent)
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
        try:
            _guestfish_write(image_path, firstrun, "/boot/firstrun.sh", chmod_mode="0755")
        except ImagerBuildError:
            _guestfish_write(image_path, firstrun, "/boot/firmware/firstrun.sh", chmod_mode="0755")


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


def list_block_devices() -> list[BlockDeviceInfo]:
    """Enumerate host block devices and safety-relevant metadata."""

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
    with source_path.open("rb") as source_handle, Path(device_path).open("wb") as device_handle:
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
    if recovery_ssh_access and recovery_ssh_access.enabled and not customize:
        raise ImagerBuildError(
            "Recovery SSH access requires image customization. Remove --skip-customize or omit recovery key options."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_filename = f"{name}-{TARGET_RPI4B}.img"
    output_path = output_dir / output_filename
    with TemporaryDirectory(dir=output_dir) as temporary_directory:
        source_path = _resolve_base_image(base_image_uri, Path(temporary_directory))
        if source_path.resolve() == output_path.resolve():
            raise ImagerBuildError("Base image path must differ from output artifact path.")
        shutil.copyfile(source_path, output_path)

    if customize:
        _customize_image(
            output_path,
            git_url=git_url,
            recovery_ssh_access=recovery_ssh_access,
        )

    sha256 = _sha256_for_file(output_path)
    size_bytes = output_path.stat().st_size
    download_uri = _build_download_uri(download_base_uri, output_filename)

    with transaction.atomic():
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
                    "recovery_ssh": {
                        "enabled": bool(customize and recovery_ssh_access and recovery_ssh_access.enabled),
                        "user": recovery_ssh_access.username if recovery_ssh_access else "",
                        "authorized_key_count": len(recovery_ssh_access.authorized_keys)
                        if recovery_ssh_access
                        else 0,
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
    )
