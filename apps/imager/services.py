"""Build services for Arthexis Raspberry Pi image artifacts."""

from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_guestfish() -> None:
    if shutil.which("guestfish"):
        return
    raise ImagerBuildError(
        "guestfish is required to customize Raspberry Pi images. Install libguestfs-tools first."
    )


def _resolve_base_image(base_image_uri: str, workspace: Path) -> Path:
    parsed = urlparse(base_image_uri)
    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme else base_image_uri).expanduser().resolve()
        if not path.exists():
            raise ImagerBuildError(f"Base image does not exist: {path}")
        return path

    if parsed.scheme not in {"http", "https"}:
        raise ImagerBuildError("Only file, http, and https base image URIs are supported.")

    destination = workspace / Path(parsed.path).name
    result = subprocess.run(
        ["curl", "-fsSL", base_image_uri, "-o", str(destination)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ImagerBuildError(f"Could not download base image: {result.stderr.strip()}")
    return destination


def _guestfish_write(image_path: Path, local_path: Path, remote_path: str, chmod_mode: str | None = None) -> None:
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
    _ensure_guestfish()
    work_dir = image_path.parent
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

    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = _resolve_base_image(base_image_uri, output_dir)
    output_filename = f"{name}-{TARGET_RPI4B}.img"
    output_path = output_dir / output_filename
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
