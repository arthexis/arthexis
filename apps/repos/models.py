"""Domain models for repositories and GitHub issues."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, TYPE_CHECKING, Any

import requests
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.release.models import Package


logger = logging.getLogger(__name__)


class GitHubRepositoryError(RuntimeError):
    """Raised when a GitHub repository operation fails."""


class GitHubRepositoryManager(EntityManager):
    def get_by_natural_key(self, owner: str, name: str):
        return self.get(owner=owner, name=name)


class GitHubRepository(Entity):
    """Source code repository reference specific to GitHub."""

    objects = GitHubRepositoryManager()

    owner = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_private = models.BooleanField(default=False)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    ssh_url = models.CharField(max_length=255, blank=True)
    default_branch = models.CharField(max_length=100, blank=True)

    API_ROOT = "https://api.github.com"
    REQUEST_TIMEOUT = 10

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.owner, self.name)

    @property
    def slug(self):  # pragma: no cover - simple representation
        return f"{self.owner}/{self.name}".strip("/")

    def __str__(self):  # pragma: no cover - simple representation
        return self.slug

    @staticmethod
    def _parse_repository_url(repository_url: str) -> tuple[str, str]:
        repository_url = (repository_url or "").strip()
        if repository_url.startswith("git@"):  # pragma: no cover - convenience
            _, _, remainder = repository_url.partition(":")
            path = remainder
        else:
            from urllib.parse import urlparse

            parsed = urlparse(repository_url)
            path = parsed.path

        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]

        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 2:
            raise ValueError(f"Invalid repository URL: {repository_url!r}")

        owner, repo = segments[-2], segments[-1]
        return owner, repo

    @classmethod
    def from_url(cls, repository_url: str) -> GitHubRepository:
        owner, repo = cls._parse_repository_url(repository_url)
        return cls(owner=owner, name=repo)

    @classmethod
    def resolve_active_repository(cls) -> GitHubRepository:
        """Return the ``(owner, repo)`` for the active package or default."""

        from apps.release.models import Package
        from apps.release.release import DEFAULT_PACKAGE

        package = Package.objects.filter(is_active=True).first()

        repository_url: str
        if package is not None:
            raw_url = getattr(package, "repository_url", "")
            if raw_url is None:
                cleaned_url = ""
            else:
                cleaned_url = str(raw_url).strip()
            repository_url = cleaned_url or DEFAULT_PACKAGE.repository_url
        else:
            repository_url = DEFAULT_PACKAGE.repository_url

        return cls.from_url(repository_url)

    @staticmethod
    def _resolve_token(package: Package | None) -> str:
        """Return the GitHub token for ``package`` or the environment."""

        if package:
            manager = getattr(package, "release_manager", None)
            if manager:
                token = getattr(manager, "github_token", "")
                if token:
                    cleaned = str(token).strip()
                    if cleaned:
                        return cleaned

        token = os.environ.get("GITHUB_TOKEN", "")
        cleaned_env = token.strip() if isinstance(token, str) else str(token).strip()
        if not cleaned_env:
            raise GitHubRepositoryError("GitHub token is not configured")
        return cleaned_env

    @staticmethod
    def _build_headers(token: str) -> Mapping[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {token}",
            "User-Agent": "arthexis-admin",
        }

    def _build_payload(self, *, private: bool | None = None, description: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "private": self.is_private if private is None else private,
        }
        description = description if description is not None else self.description
        if description:
            payload["description"] = description
        return payload

    @staticmethod
    def _extract_error_message(response: requests.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            data = {}

        message = data.get("message") or response.text or "GitHub repository request failed"
        errors = data.get("errors")
        details: list[str] = []
        if isinstance(errors, list):
            for entry in errors:
                if isinstance(entry, str):
                    details.append(entry)
                elif isinstance(entry, Mapping):
                    text = entry.get("message") or entry.get("code")
                    if text:
                        details.append(str(text))

        if details:
            message = f"{message} ({'; '.join(details)})"

        return message

    @staticmethod
    def _safe_json(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {}
        return data

    def _make_request(
        self, endpoint: str, payload: Mapping[str, object], headers: Mapping[str, str]
    ) -> requests.Response:
        response = None
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.REQUEST_TIMEOUT,
            )

            if 200 <= response.status_code < 300:
                return response

            logger.error(
                "GitHub repository creation failed for %s (%s): %s",
                self.slug or "<user>/<repo>",
                response.status_code,
                response.text,
            )
            response.raise_for_status()
            return response
        finally:
            if response is not None and not (200 <= response.status_code < 300):
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    def create_remote(
        self,
        *,
        package: Package | None,
        private: bool | None = None,
        description: str | None = None,
    ) -> str:
        """Create the repository on GitHub and return its HTML URL."""

        token = self._resolve_token(package)
        headers = self._build_headers(token)
        payload = self._build_payload(private=private, description=description)

        endpoints: list[str] = []
        owner = (self.owner or "").strip()
        if owner:
            endpoints.append(f"{self.API_ROOT}/orgs/{owner}/repos")
        endpoints.append(f"{self.API_ROOT}/user/repos")

        last_error: str | None = None

        for index, endpoint in enumerate(endpoints):
            response = None
            try:
                response = requests.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:  # pragma: no cover - network failure
                logger.exception(
                    "GitHub repository creation request failed for %s", self.slug
                )
                raise GitHubRepositoryError(str(exc)) from exc

            try:
                if 200 <= response.status_code < 300:
                    data = self._safe_json(response)
                    html_url = data.get("html_url")
                    if html_url:
                        return html_url

                    resolved_owner = (
                        data.get("owner", {}).get("login")
                        if isinstance(data.get("owner"), Mapping)
                        else owner
                    )
                    resolved_owner = (resolved_owner or owner).strip("/")
                    return f"https://github.com/{resolved_owner}/{self.name}"

                message = self._extract_error_message(response)
                logger.error(
                    "GitHub repository creation failed for %s (%s): %s",
                    self.slug or "<user>/<repo>",
                    response.status_code,
                    message,
                )
                last_error = message

                if index == 0 and owner and response.status_code in {403, 404}:
                    continue

                break
            finally:
                if response is not None:
                    close = getattr(response, "close", None)
                    if callable(close):
                        with contextlib.suppress(Exception):
                            close()

        raise GitHubRepositoryError(last_error or "GitHub repository creation failed")

    class Meta:
        verbose_name = _("GitHub Repository")
        verbose_name_plural = _("GitHub Repositories")
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_github_repository_owner_name"
            )
        ]


@dataclass(slots=True)
class GitHubIssue:
    """Represents a GitHub issue creation request."""

    owner: str
    repository: str
    token: str

    BASE_DIR = Path(__file__).resolve().parent.parent
    LOCK_DIR = BASE_DIR / ".locks" / "github-issues"
    LOCK_TTL = timedelta(hours=1)
    REQUEST_TIMEOUT = 10

    @classmethod
    def from_active_repository(cls) -> GitHubIssue:
        repository = GitHubRepository.resolve_active_repository()
        token = cls._get_github_token()
        return cls(repository.owner, repository.name, token)

    @staticmethod
    def _ensure_lock_dir() -> None:
        GitHubIssue.LOCK_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _fingerprint_digest(fingerprint: str) -> str:
        return hashlib.sha256(str(fingerprint).encode("utf-8")).hexdigest()

    @staticmethod
    def _fingerprint_path(fingerprint: str) -> Path:
        return GitHubIssue.LOCK_DIR / GitHubIssue._fingerprint_digest(fingerprint)

    @staticmethod
    def _has_recent_marker(lock_path: Path) -> bool:
        if not lock_path.exists():
            return False

        marker_age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            lock_path.stat().st_mtime, timezone.utc
        )
        return marker_age < GitHubIssue.LOCK_TTL

    @classmethod
    def _get_github_token(cls) -> str:
        """Return the configured GitHub token.

        Preference is given to the latest :class:`~core.models.PackageRelease`.
        When unavailable, fall back to the ``GITHUB_TOKEN`` environment variable.
        """

        from apps.release.models import PackageRelease
        from apps.release.release import DEFAULT_PACKAGE

        latest_release = PackageRelease.latest()
        if latest_release:
            token = latest_release.get_github_token()
            if token is not None:
                cleaned = token.strip() if isinstance(token, str) else str(token).strip()
                if cleaned:
                    return cleaned

        env_token = os.environ.get("GITHUB_TOKEN")
        if env_token is not None:
            cleaned = env_token.strip() if isinstance(env_token, str) else str(env_token).strip()
            if cleaned:
                return cleaned

        raise RuntimeError(
            f"GitHub token is not configured; set one via {DEFAULT_PACKAGE.repository_url}"
        )

    def _build_issue_payload(
        self,
        title: str,
        body: str,
        labels: Iterable[str] | None = None,
        fingerprint: str | None = None,
    ) -> Mapping[str, object] | None:
        payload: dict[str, object] = {"title": title, "body": body}

        if labels:
            deduped = list(dict.fromkeys(labels))
            if deduped:
                payload["labels"] = deduped

        if fingerprint:
            self._ensure_lock_dir()
            lock_path = self._fingerprint_path(fingerprint)
            if self._has_recent_marker(lock_path):
                logger.info("Skipping GitHub issue for active fingerprint %s", fingerprint)
                return None

            lock_path.write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8"
            )
            digest = self._fingerprint_digest(fingerprint)
            payload["body"] = f"{body}\n\n<!-- fingerprint:{digest} -->"

        return payload

    def create(
        self,
        title: str,
        body: str,
        labels: Iterable[str] | None = None,
        fingerprint: str | None = None,
    ) -> requests.Response | None:
        payload = self._build_issue_payload(title, body, labels=labels, fingerprint=fingerprint)
        if payload is None:
            return None

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {self.token}",
            "User-Agent": "arthexis-runtime-reporter",
        }
        url = f"https://api.github.com/repos/{self.owner}/{self.repository}/issues"

        response = None
        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=self.REQUEST_TIMEOUT
            )
        except Exception:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
            raise

        if not (200 <= response.status_code < 300):
            logger.error(
                "GitHub issue creation failed with status %s: %s",
                response.status_code,
                response.text,
            )
            try:
                response.raise_for_status()
            finally:
                with contextlib.suppress(Exception):
                    response.close()
            return None

        logger.info(
            "GitHub issue created for %s/%s with status %s",
            self.owner,
            self.repository,
            response.status_code,
        )
        return response


class RepositoryIssue(Entity):
    """A stored reference to a GitHub issue for a repository."""

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="issues",
        on_delete=models.CASCADE,
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=500)
    state = models.CharField(max_length=50)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    author = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "number"],
                name="unique_issue_per_repository",
            )
        ]
        verbose_name = _("Repository Issue")
        verbose_name_plural = _("Repository Issues")

    def __str__(self):  # pragma: no cover - simple representation
        return f"#{self.number} {self.title}".strip()

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime:
        parsed = parse_datetime(value) if value else None
        if parsed is None:
            parsed = timezone.now()
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        return parsed

    @staticmethod
    def _ensure_repository(repository: GitHubRepository) -> GitHubRepository:
        if repository.pk:
            return repository

        defaults = {
            "description": getattr(repository, "description", ""),
            "is_private": getattr(repository, "is_private", False),
            "html_url": getattr(repository, "html_url", ""),
            "api_url": getattr(repository, "api_url", ""),
            "ssh_url": getattr(repository, "ssh_url", ""),
            "default_branch": getattr(repository, "default_branch", ""),
        }
        repo_obj, _ = GitHubRepository.objects.get_or_create(
            owner=repository.owner, name=repository.name, defaults=defaults
        )
        return repo_obj

    @classmethod
    def _fetch_github_items(
        cls,
        *,
        token: str,
        endpoint: str,
        params: Mapping[str, object],
    ) -> Iterable[Mapping[str, object]]:
        headers = GitHubRepository._build_headers(token)
        url = endpoint
        query_params: Mapping[str, object] | None = params

        while url:
            response = None
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    params=query_params,
                    timeout=GitHubRepository.REQUEST_TIMEOUT,
                )
                query_params = None

                if not (200 <= response.status_code < 300):
                    message = GitHubRepository._extract_error_message(response)
                    raise GitHubRepositoryError(message)

                data = response.json() if callable(getattr(response, "json", None)) else []
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, Mapping):
                            yield entry

                links = getattr(response, "links", {}) or {}
                url = links.get("next", {}).get("url")
            finally:
                if response is not None:
                    close = getattr(response, "close", None)
                    if callable(close):
                        with contextlib.suppress(Exception):
                            close()

    @classmethod
    def fetch_open_issues(
        cls, repository: GitHubRepository | None = None, token: str | None = None
    ) -> tuple[int, int]:
        repository = repository or GitHubRepository.resolve_active_repository()
        token = token or GitHubIssue._get_github_token()
        repo_obj = cls._ensure_repository(repository)

        endpoint = f"{GitHubRepository.API_ROOT}/repos/{repo_obj.owner}/{repo_obj.name}/issues"
        params = {"state": "open", "per_page": 100}

        created = 0
        updated = 0

        for item in cls._fetch_github_items(
            token=token, endpoint=endpoint, params=params
        ):
            if "pull_request" in item:
                continue

            number = item.get("number")
            if not isinstance(number, int):
                continue

            defaults = {
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "html_url": item.get("html_url") or "",
                "api_url": item.get("url") or "",
                "author": (item.get("user") or {}).get("login") or "",
                "created_at": cls._parse_timestamp(item.get("created_at")),
                "updated_at": cls._parse_timestamp(item.get("updated_at")),
            }

            _, was_created = cls.objects.update_or_create(
                repository=repo_obj, number=number, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return created, updated


class RepositoryPullRequest(Entity):
    """A stored reference to a GitHub pull request for a repository."""

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="pull_requests",
        on_delete=models.CASCADE,
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=500)
    state = models.CharField(max_length=50)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    author = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    merged_at = models.DateTimeField(null=True, blank=True)
    source_branch = models.CharField(max_length=255, blank=True)
    target_branch = models.CharField(max_length=255, blank=True)
    is_draft = models.BooleanField(default=False)

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "number"],
                name="unique_pull_request_per_repository",
            )
        ]
        verbose_name = _("Repository Pull Request")
        verbose_name_plural = _("Repository Pull Requests")

    def __str__(self):  # pragma: no cover - simple representation
        return f"PR #{self.number} {self.title}".strip()

    @classmethod
    def fetch_open_pull_requests(
        cls, repository: GitHubRepository | None = None, token: str | None = None
    ) -> tuple[int, int]:
        repository = repository or GitHubRepository.resolve_active_repository()
        token = token or GitHubIssue._get_github_token()
        repo_obj = RepositoryIssue._ensure_repository(repository)

        endpoint = f"{GitHubRepository.API_ROOT}/repos/{repo_obj.owner}/{repo_obj.name}/pulls"
        params = {"state": "open", "per_page": 100}

        created = 0
        updated = 0

        for item in RepositoryIssue._fetch_github_items(
            token=token, endpoint=endpoint, params=params
        ):
            number = item.get("number")
            if not isinstance(number, int):
                continue

            defaults = {
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "html_url": item.get("html_url") or "",
                "api_url": item.get("url") or "",
                "author": (item.get("user") or {}).get("login") or "",
                "created_at": RepositoryIssue._parse_timestamp(item.get("created_at")),
                "updated_at": RepositoryIssue._parse_timestamp(item.get("updated_at")),
                "merged_at": RepositoryIssue._parse_timestamp(item.get("merged_at"))
                if item.get("merged_at")
                else None,
                "source_branch": (item.get("head") or {}).get("ref") or "",
                "target_branch": (item.get("base") or {}).get("ref") or "",
                "is_draft": bool(item.get("draft")),
            }

            _, was_created = cls.objects.update_or_create(
                repository=repo_obj, number=number, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return created, updated

