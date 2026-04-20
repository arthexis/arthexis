"""Build services for Arthexis Raspberry Pi image artifacts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
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

BOOTSTRAP_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

APP_HOME=/opt/arthexis
if [ ! -d "$APP_HOME/.git" ]; then
  git clone --depth 1 "${ARTHEXIS_GIT_URL}" "$APP_HOME"
fi

cd "$APP_HOME"
./env-refresh.sh --deps-only
./start.sh
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

chmod +x /usr/local/bin/arthexis-bootstrap.sh
systemctl daemon-reload
systemctl enable arthexis-bootstrap.service
systemctl start arthexis-bootstrap.service
rm -f /boot/firstrun.sh /boot/firmware/firstrun.sh
"""


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


def _resolve_base_image(base_image_uri: str, workspace: Path) -> Path:
    """Resolve local/file/http(s) base image inputs to a local filesystem path."""

    parsed = urlparse(base_image_uri)
    local_path = _normalize_local_source_path(base_image_uri, parsed)
    if local_path is not None:
        path = local_path.expanduser().resolve()
        if not path.exists():
            raise ImagerBuildError(f"Base image does not exist: {path}")
        return path

    if parsed.scheme not in {"http", "https"}:
        raise ImagerBuildError("Only file, http, and https base image URIs are supported.")

    destination_name = Path(unquote(parsed.path)).name or "base-image.img"
    destination = workspace / destination_name
    try:
        _download_remote_base_image(base_image_uri, destination)
    except (HTTPError, URLError) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise ImagerBuildError(f"Could not download base image: {reason}") from exc
    return destination


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


def _guestfish_write(image_path: Path, local_path: Path, remote_path: str, chmod_mode: str | None = None) -> None:
    """Upload a local file into the disk image using guestfish."""

    script_parts = [
        f"upload {shlex.quote(str(local_path))} {shlex.quote(remote_path)}",
    ]
    if chmod_mode:
        script_parts.append(f"chmod {chmod_mode} {shlex.quote(remote_path)}")
    script = "\n".join(script_parts) + "\n"
    result = subprocess.run(
        ["guestfish", "--rw", "-a", str(image_path), "-i"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ImagerBuildError(result.stderr.strip() or "guestfish failed while writing files")


def _customize_image(image_path: Path, *, git_url: str) -> None:
    """Inject bootstrap scripts and systemd units into the image."""

    _ensure_guestfish()
    with TemporaryDirectory(dir=image_path.parent) as temporary_directory:
        work_dir = Path(temporary_directory)
        bootstrap = work_dir / "arthexis-bootstrap.sh"
        service = work_dir / "arthexis-bootstrap.service"
        firstrun = work_dir / "firstrun.sh"

        bootstrap.write_text(BOOTSTRAP_SCRIPT, encoding="utf-8")
        service.write_text(SYSTEMD_SERVICE.format(git_url=git_url), encoding="utf-8")
        firstrun.write_text(FIRST_RUN_SCRIPT, encoding="utf-8")

        _guestfish_write(image_path, bootstrap, "/usr/local/bin/arthexis-bootstrap.sh", chmod_mode="0755")
        _guestfish_write(image_path, service, "/etc/systemd/system/arthexis-bootstrap.service")
        try:
            _guestfish_write(image_path, firstrun, "/boot/firstrun.sh", chmod_mode="0755")
        except ImagerBuildError:
            _guestfish_write(image_path, firstrun, "/boot/firmware/firstrun.sh", chmod_mode="0755")


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

        pkname_result = subprocess.run(
            ["lsblk", "-n", "-o", "PKNAME", root_source],
            capture_output=True,
            text=True,
            check=False,
        )
        pkname = pkname_result.stdout.strip()
        if pkname_result.returncode == 0 and pkname:
            return f"/dev/{pkname}"

        type_result = subprocess.run(
            ["lsblk", "-n", "-o", "TYPE", root_source],
            capture_output=True,
            text=True,
            check=False,
        )
        if type_result.returncode == 0 and type_result.stdout.strip() == "disk":
            return root_source
    except FileNotFoundError:
        return None
    return None


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
        children = entry.get("children", [])
        mountpoints = [mount for mount in (entry.get("mountpoints") or []) if mount]
        mountpoints.extend(
            mount
            for child in children
            for mount in (child.get("mountpoints") or [])
            if mount
        )
        partitions = [child.get("path", "") for child in children if child.get("path")]
        devices.append(
            BlockDeviceInfo(
                path=str(entry.get("path", "")),
                size_bytes=int(entry.get("size") or 0),
                transport=str(entry.get("tran") or ""),
                removable=bool(entry.get("rm")),
                mountpoints=sorted(set(mountpoints)),
                partitions=partitions,
                protected=str(entry.get("path", "")) == root_disk,
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
) -> BuildResult:
    """Build and register a Raspberry Pi 4B Arthexis image artifact."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ImagerBuildError(
            "Artifact name must start with an alphanumeric character and use only letters, numbers, dot, underscore, or hyphen."
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
        _customize_image(output_path, git_url=git_url)

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
                    "bootstrap_service": "arthexis-bootstrap.service",
                    "bootstrap_script": "/usr/local/bin/arthexis-bootstrap.sh",
                    "first_boot_script": "firstrun.sh",
                    "git_url": git_url,
                },
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
    )
