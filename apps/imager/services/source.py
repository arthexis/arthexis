"""Base image source URI validation, download, and archive extraction."""

from __future__ import annotations

import gzip
import ipaddress
import lzma
import re
import shutil
import socket
import zipfile
from pathlib import Path
from urllib.parse import ParseResult, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings

from .models import ImagerBuildError


def _normalize_local_source_path(
    base_image_uri: str, parsed_uri: ParseResult
) -> Path | None:
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

    raise ImagerBuildError(
        f"Remote base image URL '{base_image_uri}' exceeded redirect limit."
    )


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
                members = [
                    member for member in archive.infolist() if not member.is_dir()
                ]
                image_members = [
                    member
                    for member in members
                    if Path(member.filename).suffix.lower() == ".img"
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
        raise ImagerBuildError(
            "Only file, http, and https base image URIs are supported."
        )

    destination_name = Path(unquote(parsed.path)).name or "base-image.img"
    destination = workspace / destination_name
    try:
        _download_remote_base_image(base_image_uri, destination)
    except OSError as exc:
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
        addrinfos = socket.getaddrinfo(
            host, parsed.port or 443, type=socket.SOCK_STREAM
        )
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
