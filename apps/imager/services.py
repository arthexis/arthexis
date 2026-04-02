"""Build services for Arthexis Raspberry Pi image artifacts."""

from __future__ import annotations

import hashlib
import ipaddress
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


def _sha256_for_file(path: Path) -> str:
    """Compute the SHA-256 checksum for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
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
