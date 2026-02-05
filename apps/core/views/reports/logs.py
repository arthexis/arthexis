from __future__ import annotations

import io
import logging
import os
import uuid
import zipfile
from pathlib import Path

import requests
from django.conf import settings

from apps.loggers.paths import select_log_dir

from .common import MAX_PYPI_PUBLISH_LOG_SIZE

logger = logging.getLogger(__name__)


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

    logger.warning(
        "Release log directory %s is not writable: %s", preferred, error
    )

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

    settings.LOG_DIR = fallback
    warning = (
        f"Release log directory {preferred} is not writable; using {fallback}"
    )
    logger.warning(warning)
    return fallback, warning


def _github_headers(token: str | None) -> dict[str, str]:
    if not token:
        raise Exception("GitHub token is required to export artifacts")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_request(
    method: str,
    url: str,
    *,
    token: str | None,
    expected_status: set[int],
    **kwargs,
) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.update(_github_headers(token))
    response = requests.request(method, url, headers=headers, **kwargs)
    if response.status_code not in expected_status:
        detail = response.text.strip()
        raise Exception(
            f"GitHub API request failed ({response.status_code}): {detail}"
        )
    return response


def _download_publish_workflow_logs(
    *,
    owner: str,
    repo: str,
    run_id: int,
    token: str | None,
) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
    response = _github_request(
        "get",
        url,
        token=token,
        expected_status={200},
        allow_redirects=True,
        timeout=30,
    )
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    sections: list[str] = []
    for name in sorted(archive.namelist()):
        if not name.endswith(".txt"):
            continue
        data = archive.read(name).decode("utf-8", errors="replace")
        sections.append(f"--- {name} ---\n{data}")
    return "\n\n".join(sections)


def _truncate_publish_log(
    log_text: str, *, limit: int = MAX_PYPI_PUBLISH_LOG_SIZE
) -> str:
    if len(log_text) <= limit:
        return log_text
    trimmed = log_text[-limit:]
    return f"[truncated; last {limit} of {len(log_text)} chars]\n{trimmed}"
