from __future__ import annotations

import io
import logging
import os
import threading
import uuid
import zipfile
from pathlib import Path

import requests
from django.conf import settings

from utils.loggers.paths import select_log_dir

from .common import MAX_PYPI_PUBLISH_LOG_SIZE

logger = logging.getLogger(__name__)
log_dir_lock = threading.Lock()


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def _release_log_name(package_name: str, version: str) -> str:
    return f"pr.{package_name}.v{version}.log"


def _ensure_log_directory(path: Path) -> tuple[bool, OSError | None]:
    """Return whether ``path`` is writable along with the triggering error."""

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, exc

    probe = path / f".permcheck_{uuid.uuid4().hex}"
    try:
        with probe.open("w", encoding="utf-8") as fh:
            fh.write("")
    except OSError as exc:
        return False, exc
    else:
        try:
            probe.unlink()
        except OSError:
            pass
        return True, None


def _resolve_release_log_dir(preferred: Path) -> tuple[Path, str | None]:
    """Return a writable log directory for the release publish flow."""

    writable, error = _ensure_log_directory(preferred)
    if writable:
        return preferred, None

    logger.warning("Release log directory %s is not writable: %s", preferred, error)

    with log_dir_lock:
        env_override = os.environ.pop("ARTHEXIS_LOG_DIR", None)
        fallback = select_log_dir(Path(settings.BASE_DIR))
        if env_override is not None:
            if Path(env_override) == fallback:
                os.environ["ARTHEXIS_LOG_DIR"] = env_override
            else:
                os.environ["ARTHEXIS_LOG_DIR"] = str(fallback)

    if fallback == preferred:
        if error:
            raise error
        raise PermissionError(f"Release log directory {preferred} is not writable")

    fallback_writable, fallback_error = _ensure_log_directory(fallback)
    if not fallback_writable:
        raise fallback_error or PermissionError(
            f"Release log directory {fallback} is not writable"
        )

    warning = f"Release log directory {preferred} was not writable; using {fallback}."
    logger.warning(warning)
    return fallback, warning


def _fetch_github_release_asset(url: str, token: str | None = None) -> bytes:
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content


def _zip_bytes(files: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return buffer.getvalue()
