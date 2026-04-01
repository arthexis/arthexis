"""Build services for Arthexis Raspberry Pi image artifacts."""

from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import URLError
from urllib.parse import ParseResult, unquote, urlparse
from urllib.request import urlopen

from django.conf import settings
from django.db import transaction

from apps.imager.models import RaspberryPiImageArtifact

TARGET_RPI4B = "rpi-4b"
DEFAULT_REMOTE_DOWNLOAD_TIMEOUT_SECONDS = 30
REMOTE_DOWNLOAD_CHUNK_SIZE_BYTES = 64 * 1024

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
    timeout_seconds = int(
        getattr(
            settings,
            "IMAGER_REMOTE_DOWNLOAD_TIMEOUT_SECONDS",
            DEFAULT_REMOTE_DOWNLOAD_TIMEOUT_SECONDS,
        )
    )
    max_bytes = int(
        getattr(
            settings,
            "IMAGER_REMOTE_IMAGE_MAX_BYTES",
            2 * 1024 * 1024 * 1024,
        )
    )

    if max_bytes <= 0:
        raise ImagerBuildError("Configured IMAGER_REMOTE_IMAGE_MAX_BYTES must be greater than zero.")

    def _coerce_content_length(raw_value: Any) -> int | None:
        try:
            if raw_value is None:
                return None
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    try:
        with urlopen(base_image_uri, timeout=timeout_seconds) as response, destination.open("wb") as output_handle:
            content_length = _coerce_content_length(response.headers.get("Content-Length"))
            if content_length is not None and content_length > max_bytes:
                raise ImagerBuildError(
                    "Could not download base image: remote payload is "
                    f"{content_length} bytes, exceeding IMAGER_REMOTE_IMAGE_MAX_BYTES={max_bytes} bytes."
                )

            total_bytes = 0
            while True:
                chunk = response.read(REMOTE_DOWNLOAD_CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise ImagerBuildError(
                        "Could not download base image: streamed payload exceeded "
                        f"IMAGER_REMOTE_IMAGE_MAX_BYTES={max_bytes} bytes "
                        f"after reading {total_bytes} bytes."
                    )
                output_handle.write(chunk)
    except URLError as exc:
        reason = getattr(exc, "reason", str(exc))
        if isinstance(reason, TimeoutError):
            raise ImagerBuildError(
                "Could not download base image: timed out after "
                f"{timeout_seconds} seconds (IMAGER_REMOTE_DOWNLOAD_TIMEOUT_SECONDS)."
            ) from exc
        raise ImagerBuildError(f"Could not download base image: {reason}") from exc
    return destination


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

    base = (download_base_uri or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{output_filename}"


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
