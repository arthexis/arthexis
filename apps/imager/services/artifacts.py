"""Artifact metadata and URL helpers for imager build services."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlparse

from .models import (
    TARGET_RPI4B,
    BuildEngineProfile,
    ImagerBuildError,
    ImageSizeAdjustment,
    NetworkProfileInfo,
    SuiteBundleInfo,
)

LOCAL_HTTP_SCHEME = "http"


def _sanitize_storage_options(storage_options: dict[str, object]) -> dict[str, object]:
    """Mask sensitive storage configuration values before persisting metadata."""

    sensitive_fragments = (
        "access_key",
        "account_key",
        "connection_string",
        "password",
        "private_key",
        "secret",
        "shared_key",
        "token",
    )

    def is_sensitive_key(key: object) -> bool:
        return any(fragment in str(key).lower() for fragment in sensitive_fragments)

    def sanitize_value(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: "***" if is_sensitive_key(key) else sanitize_value(nested_value)
                for key, nested_value in value.items()
            }
        if isinstance(value, list):
            return [sanitize_value(item) for item in value]
        return value

    return {
        key: "***" if is_sensitive_key(key) else sanitize_value(value)
        for key, value in storage_options.items()
    }


def _sha256_for_file(
    path: Path, *, progress_callback: Callable[[int, int], None] | None = None
) -> str:
    """Compute the SHA-256 checksum for a file."""

    digest = hashlib.sha256()
    total_size = path.stat().st_size if progress_callback is not None else 0
    read_bytes = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            if progress_callback is not None:
                read_bytes += len(chunk)
                progress_callback(read_bytes, total_size)
    return digest.hexdigest()


def _sha256_for_prefix(
    path: Path,
    *,
    size_bytes: int,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """Compute SHA-256 for the first ``size_bytes`` bytes of a file/device."""

    digest = hashlib.sha256()
    remaining = size_bytes
    read_bytes = 0
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
            if progress_callback is not None:
                read_bytes += len(chunk)
                progress_callback(read_bytes, size_bytes)
    if remaining != 0:
        raise ImagerBuildError(
            f"Could not read expected {size_bytes} bytes from {path}."
        )
    return digest.hexdigest()


def _coerce_profile_metadata(
    profile_metadata: dict[str, object] | None,
) -> dict[str, object]:
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


def _network_profiles_metadata(
    network_profiles: tuple[NetworkProfileInfo, ...],
) -> dict[str, object]:
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


def _image_size_metadata(adjustment: ImageSizeAdjustment) -> dict[str, object]:
    """Return JSON-safe metadata for disk sizing actions."""

    return {
        "minimum_size_bytes": adjustment.requested_size_bytes,
        "original_size_bytes": adjustment.original_size_bytes,
        "final_size_bytes": adjustment.final_size_bytes,
        "image_extended": adjustment.image_extended,
        "root_partition_expanded": adjustment.root_partition_expanded,
        "root_partition_device": adjustment.root_partition_device
        if adjustment.root_partition_expanded
        else "",
    }


def _format_url_host(host: str) -> str:
    """Bracket IPv6 hosts for URL construction."""

    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _build_local_http_url(*, host: str, port: int, path: str = "/") -> str:
    """Build a URL for the suite's transient local HTTP image-serving paths."""

    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{LOCAL_HTTP_SCHEME}://{_format_url_host(host)}:{port}{normalized_path}"


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
            raise ImagerBuildError(
                "Serve base URL must use http or https and include a host."
            )
        normalized_path = f"{parsed_base.path.rstrip('/')}/{quote(output_filename)}"
        return parsed_base._replace(path=normalized_path).geturl()

    advertised_host = _format_url_host((url_host or "127.0.0.1").strip())
    return _build_local_http_url(
        host=advertised_host,
        port=port,
        path=f"/{quote(output_filename)}",
    )
