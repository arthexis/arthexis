"""guestfish command construction and image partition expansion helpers."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import (
    DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES,
    IMAGE_PARTITION_SECTOR_SIZE_BYTES,
    RPI_ROOT_DISK_DEVICE,
    RPI_ROOT_PARTITION_DEVICE,
    RPI_ROOT_PARTITION_NUMBER,
    ImagerBuildError,
    ImageSizeAdjustment,
)


def _ensure_guestfish() -> None:
    """Ensure guestfish is available for image resize/customization steps."""

    if shutil.which("guestfish"):
        return
    raise ImagerBuildError(
        "guestfish is required to resize or customize Raspberry Pi images. "
        "Install libguestfs-tools first."
    )


def _run_guestfish_command(
    image_path: Path,
    command: list[str],
    script: str,
    *,
    error_message: str,
) -> None:
    """Run a guestfish command with image-local temporary and cache directories."""

    image_path = image_path.resolve()
    cache_dir = (image_path.parent / ".libguestfs-cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=image_path.parent) as temp_dir:
        temp_dir = str(Path(temp_dir).resolve())
        guestfish_env = os.environ.copy()
        guestfish_env["TMPDIR"] = temp_dir
        guestfish_env["LIBGUESTFS_TMPDIR"] = temp_dir
        guestfish_env["LIBGUESTFS_CACHEDIR"] = str(cache_dir)
        result = subprocess.run(
            command,
            input=script,
            text=True,
            capture_output=True,
            check=False,
            env=guestfish_env,
        )
        if result.returncode != 0:
            raise ImagerBuildError(result.stderr.strip() or error_message)


def _run_guestfish_script(image_path: Path, script: str, *, error_message: str) -> None:
    """Run a guestfish script with image inspection and mounted filesystems."""

    image_path = image_path.resolve()
    _run_guestfish_command(
        image_path,
        ["guestfish", "--rw", "-a", str(image_path), "-i"],
        script,
        error_message=error_message,
    )


def _run_guestfish_raw_script(
    image_path: Path, script: str, *, error_message: str
) -> None:
    """Run a guestfish script without image inspection or automatic mounts."""

    image_path = image_path.resolve()
    _run_guestfish_command(
        image_path,
        ["guestfish", "--rw", "-a", str(image_path)],
        script,
        error_message=error_message,
    )


def _normalize_minimum_image_size_bytes(
    minimum_size_bytes: int | None,
    *,
    customize: bool,
) -> int:
    """Return the effective raw-image minimum size for this build."""

    if minimum_size_bytes is None:
        return DEFAULT_CUSTOMIZED_IMAGE_MINIMUM_SIZE_BYTES if customize else 0
    if type(minimum_size_bytes) is not int:
        raise ImagerBuildError(
            "minimum_image_size_bytes must be an integer byte count."
        )
    normalized_size = minimum_size_bytes
    if normalized_size < 0:
        raise ImagerBuildError(
            "minimum_image_size_bytes must be greater than or equal to zero."
        )
    return normalized_size


def _expand_root_partition_to_image(image_path: Path, *, end_sector: int) -> None:
    """Grow the Raspberry Pi root partition and ext filesystem to the image size."""

    if end_sector <= 0:
        raise ImagerBuildError(
            "Image is too small to calculate a valid root partition end sector."
        )
    _ensure_guestfish()
    script = "\n".join(
        [
            "run",
            f"part-resize {RPI_ROOT_DISK_DEVICE} {RPI_ROOT_PARTITION_NUMBER} {end_sector}",
            f"blockdev-rereadpt {RPI_ROOT_DISK_DEVICE}",
            f"e2fsck-f {RPI_ROOT_PARTITION_DEVICE}",
            f"resize2fs {RPI_ROOT_PARTITION_DEVICE}",
        ]
    )
    _run_guestfish_raw_script(
        image_path,
        script + "\n",
        error_message="guestfish failed while expanding the image root filesystem",
    )


def _ensure_image_minimum_size(
    image_path: Path,
    *,
    minimum_size_bytes: int,
) -> ImageSizeAdjustment:
    """Extend a raw image and expand its root filesystem when sizing is enabled."""

    original_size = image_path.stat().st_size
    if minimum_size_bytes <= 0:
        return ImageSizeAdjustment(
            requested_size_bytes=0,
            original_size_bytes=original_size,
            final_size_bytes=original_size,
            image_extended=False,
            root_partition_expanded=False,
        )

    image_extended = original_size < minimum_size_bytes
    if image_extended:
        try:
            with image_path.open("r+b") as image_file:
                image_file.truncate(minimum_size_bytes)
        except OSError as exc:
            raise ImagerBuildError(
                f"Failed to extend image file to {minimum_size_bytes} bytes: {exc}"
            ) from exc

        expanded_size = image_path.stat().st_size
        end_sector = (expanded_size // IMAGE_PARTITION_SECTOR_SIZE_BYTES) - 1
        _expand_root_partition_to_image(image_path, end_sector=end_sector)
    final_size = image_path.stat().st_size
    return ImageSizeAdjustment(
        requested_size_bytes=minimum_size_bytes,
        original_size_bytes=original_size,
        final_size_bytes=final_size,
        image_extended=image_extended,
        root_partition_expanded=image_extended,
    )


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
