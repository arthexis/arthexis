from __future__ import annotations

import logging
from functools import lru_cache
from urllib.parse import urlparse

from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from config.request_utils import is_https_request
from apps.release import git_utils


logger = logging.getLogger(__name__)


def _github_repo_path(remote_url: str | None) -> str:
    """Return the ``owner/repo`` path for a GitHub *remote_url* if possible."""

    if not remote_url:
        return ""

    normalized = remote_url.strip()
    if not normalized:
        return ""

    path = ""
    if normalized.startswith("git@"):
        host, _, remainder = normalized.partition(":")
        if "github.com" not in host.lower():
            return ""
        path = remainder
    else:
        parsed = urlparse(normalized)
        if "github.com" not in parsed.netloc.lower():
            return ""
        path = parsed.path

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]

    if not path:
        return ""

    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return ""

    owner, repo = segments[-2], segments[-1]
    return f"{owner}/{repo}"


@lru_cache()
def _github_commit_url_base() -> str:
    """Return the GitHub commit URL template for the configured repository."""

    try:
        remote_url = git_utils.git_remote_url()
    except FileNotFoundError:  # pragma: no cover - depends on environment setup
        logger.debug("Skipping GitHub commit URL generation; git executable not found")
        remote_url = None

    repo_path = _github_repo_path(remote_url)
    if not repo_path:
        return ""
    return f"https://github.com/{repo_path}/commit/{{sha}}"


def _github_commit_url(sha: str) -> str:
    """Return the GitHub commit URL for *sha* when available."""

    base = _github_commit_url_base()
    clean_sha = (sha or "").strip()
    if not base or not clean_sha:
        return ""
    return base.replace("{sha}", clean_sha)


def _upgrade_redirect(request, fallback: str) -> HttpResponseRedirect:
    """Return a safe redirect response for upgrade-related form submissions."""

    candidate = (request.POST.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=is_https_request(request),
    ):
        return HttpResponseRedirect(candidate)
    return HttpResponseRedirect(fallback)
