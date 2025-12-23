from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime as datetime_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from django.core import serializers
from django.core.exceptions import ValidationError
from django.db import DatabaseError, models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity, EntityManager
from apps.core.fixtures import ensure_seed_data_flags
from apps.locals import user_data
from utils import revision as revision_utils

from ..release import Credentials, Package as ReleasePackage, RepositoryTarget
from .package import Package
from .release_manager import ReleaseManager

logger = logging.getLogger(__name__)


class PackageReleaseManager(EntityManager):
    def get_by_natural_key(self, package, version):
        return self.get(package__name=package, version=version)


class PackageRelease(Entity):
    """Store metadata for a specific package version."""

    DEV_SUFFIX = "+d"
    LEGACY_DEV_SUFFIX = "+"

    _PATCH_BITS = 12
    _MINOR_BITS = 12
    _PATCH_MASK = (1 << _PATCH_BITS) - 1
    _MINOR_MASK = (1 << _MINOR_BITS) - 1
    _MINOR_SHIFT = _PATCH_BITS
    _MAJOR_SHIFT = _PATCH_BITS + _MINOR_BITS

    objects = PackageReleaseManager()

    def natural_key(self):
        return (self.package.name, self.version)

    @staticmethod
    def _format_patch_with_epoch(parsed: "Version", *, increment: int = 1) -> str:
        """Return a patch-bumped version string preserving the epoch."""

        bumped_patch = f"{parsed.major}.{parsed.minor}.{parsed.micro + increment}"
        if parsed.epoch:
            return f"{parsed.epoch}!{bumped_patch}"
        return bumped_patch

    @staticmethod
    def normalize_version(version: str) -> str:
        """Return a release-safe version without local identifiers.

        Versions containing a ``+`` are treated as local builds and bumped to
        the next patch release.
        """
        from packaging.version import InvalidVersion, Version

        text = (version or "").strip()
        if "+" not in text:
            return text

        cleaned = PackageRelease.strip_dev_suffix(text)
        try:
            parsed = Version(cleaned)
        except InvalidVersion:
            parts = cleaned.split(".") if cleaned else []
            for index in range(len(parts) - 1, -1, -1):
                segment = parts[index]
                if segment.isdigit():
                    parts[index] = str(int(segment) + 1)
                    return ".".join(parts)
            return cleaned or text

        return PackageRelease._format_patch_with_epoch(parsed)

    @staticmethod
    def strip_dev_suffix(version: str) -> str:
        text = (version or "").strip()
        for suffix in (PackageRelease.DEV_SUFFIX, PackageRelease.LEGACY_DEV_SUFFIX):
            if text.endswith(suffix):
                return text[: -len(suffix)]
        return text

    class Severity(models.TextChoices):
        NORMAL = "normal", _("Normal")
        LOW = "low", _("Low")
        CRITICAL = "critical", _("Critical")

    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="releases"
    )
    release_manager = models.ForeignKey(
        ReleaseManager, on_delete=models.SET_NULL, null=True, blank=True
    )
    version = models.CharField(max_length=20, default="0.0.0")
    revision = models.CharField(
        max_length=40, blank=True, default=revision_utils.get_revision, editable=False
    )
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.NORMAL,
        help_text=_("Controls the expected urgency for auto-upgrades."),
    )
    pypi_url = models.URLField("PyPI URL", blank=True, editable=False)
    github_url = models.URLField("GitHub URL", blank=True, editable=False)
    release_on = models.DateTimeField(blank=True, null=True, editable=False)
    scheduled_date = models.DateField(blank=True, null=True)
    scheduled_time = models.TimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Package Release"
        verbose_name_plural = "Package Releases"
        get_latest_by = "version"
        constraints = [
            models.UniqueConstraint(
                fields=("package", "version"), name="unique_package_version"
            )
        ]

    @classmethod
    def dump_fixture(cls) -> None:
        base = Path("apps/core/fixtures")
        base.mkdir(parents=True, exist_ok=True)
        existing = {path.name: path for path in base.glob("releases__*.json")}
        expected: set[str] = set()
        for release in cls.objects.all():
            name = f"releases__packagerelease_{release.version.replace('.', '_')}.json"
            path = base / name
            content = ensure_seed_data_flags(
                serializers.serialize(
                    "json",
                    [release],
                    use_natural_foreign_keys=True,
                    use_natural_primary_keys=True,
                )
            )
            data = json.dumps(json.loads(content), indent=2) + "\n"
            expected.add(name)
            try:
                current = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                current = None
            if current != data:
                path.write_text(data, encoding="utf-8")
        for old_name, old_path in existing.items():
            if old_name not in expected and old_path.exists():
                old_path.unlink()

    def delete(self, using=None, keep_parents=False):
        user_data.delete_user_fixture(self)
        super().delete(using=using, keep_parents=keep_parents)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.package.name} {self.version}"

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` built from the package."""
        return self.package.to_package()

    def clean(self):
        super().clean()
        has_date = bool(self.scheduled_date)
        has_time = self.scheduled_time is not None
        if has_date != has_time:
            raise ValidationError(
                {
                    "scheduled_date": _(
                        "Scheduled Date and Scheduled Time must both be provided."
                    ),
                    "scheduled_time": _(
                        "Scheduled Date and Scheduled Time must both be provided."
                    ),
                }
            )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        normalized_version = type(self).normalize_version(self.version)
        if normalized_version != self.version:
            self.version = normalized_version
            if update_fields:
                updated_fields = set(update_fields)
                updated_fields.add("version")
                kwargs["update_fields"] = list(updated_fields)
        super().save(*args, **kwargs)

    def clear_schedule(self, *, save: bool = True) -> None:
        """Remove any scheduled release metadata."""

        self.scheduled_date = None
        self.scheduled_time = None
        if save:
            self.save(update_fields=["scheduled_date", "scheduled_time"])

    @property
    def scheduled_datetime(self) -> datetime_datetime | None:
        """Return the combined scheduled datetime when available."""

        if not self.scheduled_date or self.scheduled_time is None:
            return None
        combined = datetime_datetime.combine(
            self.scheduled_date, self.scheduled_time
        )
        return timezone.make_aware(
            combined, timezone.get_current_timezone()
        )

    def to_credentials(
        self, user: models.Model | None = None
    ) -> Credentials | None:
        """Return :class:`Credentials` from available release managers."""

        manager_candidates: list[ReleaseManager] = []

        for candidate in (self.release_manager, self.package.release_manager):
            if candidate and candidate not in manager_candidates:
                manager_candidates.append(candidate)

        if user is not None and getattr(user, "is_authenticated", False):
            try:
                user_manager = ReleaseManager.objects.get(user=user)
            except ReleaseManager.DoesNotExist:
                user_manager = None
            else:
                if user_manager not in manager_candidates:
                    manager_candidates.append(user_manager)

        for manager in manager_candidates:
            creds = manager.to_credentials()
            if creds and creds.has_auth():
                return creds

        token = (os.environ.get("PYPI_API_TOKEN") or "").strip()
        username = (os.environ.get("PYPI_USERNAME") or "").strip()
        password = (os.environ.get("PYPI_PASSWORD") or "").strip()

        if token:
            return Credentials(token=token)
        if username and password:
            return Credentials(username=username, password=password)
        return None

    def get_github_token(self) -> str | None:
        """Return GitHub token from the associated release manager or environment."""
        manager = self.release_manager or self.package.release_manager
        if manager and manager.github_token:
            return manager.github_token
        return os.environ.get("GITHUB_TOKEN")

    def build_publish_targets(
        self, user: models.Model | None = None
    ) -> list[RepositoryTarget]:
        """Return repository targets for publishing this release."""

        manager = self.release_manager or self.package.release_manager
        targets: list[RepositoryTarget] = []

        package_targets = [
            repo.to_target()
            for repo in self.package.package_repositories.all().order_by("pk")
        ]

        env_primary = os.environ.get("PYPI_REPOSITORY_URL", "")
        primary_url = env_primary.strip()

        if package_targets:
            primary_creds = self.to_credentials(user=user)
            for index, target in enumerate(package_targets):
                repository_url = target.repository_url
                if index == 0 and primary_url:
                    repository_url = primary_url

                credentials = target.credentials
                if credentials is None and primary_creds and primary_creds.has_auth():
                    credentials = primary_creds

                targets.append(
                    RepositoryTarget(
                        name=target.name,
                        repository_url=repository_url,
                        credentials=credentials,
                        verify_availability=target.verify_availability,
                        extra_args=target.extra_args,
                    )
                )

            return targets

        primary_creds = self.to_credentials(user=user)
        targets.append(
            RepositoryTarget(
                name="PyPI",
                repository_url=primary_url or None,
                credentials=primary_creds,
                verify_availability=True,
            )
        )

        secondary_url = ""
        if manager and getattr(manager, "secondary_pypi_url", ""):
            secondary_url = manager.secondary_pypi_url.strip()
        if not secondary_url:
            env_secondary = os.environ.get("PYPI_SECONDARY_URL", "")
            secondary_url = env_secondary.strip()
        if not secondary_url:
            return targets

        def _clone_credentials(creds: Credentials | None) -> Credentials | None:
            if creds is None or not creds.has_auth():
                return None
            return Credentials(
                token=creds.token,
                username=creds.username,
                password=creds.password,
            )

        github_token = self.get_github_token()
        github_username = None
        if manager and manager.pypi_username:
            github_username = manager.pypi_username.strip() or None
        env_secondary_username = os.environ.get("PYPI_SECONDARY_USERNAME")
        env_secondary_password = os.environ.get("PYPI_SECONDARY_PASSWORD")
        if not github_username:
            github_username = (
                os.environ.get("GITHUB_USERNAME")
                or os.environ.get("GITHUB_ACTOR")
                or (env_secondary_username.strip() if env_secondary_username else None)
            )

        password_candidate = github_token or (
            env_secondary_password.strip() if env_secondary_password else None
        )

        secondary_creds: Credentials | None = None
        if github_username and password_candidate:
            secondary_creds = Credentials(
                username=github_username,
                password=password_candidate,
            )
        else:
            secondary_creds = _clone_credentials(primary_creds)

        if secondary_creds and secondary_creds.has_auth():
            name = "GitHub Packages" if github_token else "Secondary repository"
            targets.append(
                RepositoryTarget(
                    name=name,
                    repository_url=secondary_url,
                    credentials=secondary_creds,
                )
            )

        return targets

    def github_package_url(self) -> str | None:
        """Return the GitHub Packages URL for this release if determinable."""

        repo_url = self.package.repository_url
        if not repo_url:
            return None
        parsed = urlparse(repo_url)
        if "github.com" not in parsed.netloc.lower():
            return None
        path = parsed.path.strip("/")
        if not path:
            return None
        if path.endswith(".git"):
            path = path[: -len(".git")]
        return (
            f"https://github.com/{path}/pkgs/pypi/{self.package.name}"
            f"/versions?version={quote_plus(self.version)}"
        )

    @property
    def migration_number(self) -> int:
        """Return the migration number derived from the version bits."""
        from packaging.version import Version

        v = Version(self.version)
        return (
            (v.major << self._MAJOR_SHIFT)
            | (v.minor << self._MINOR_SHIFT)
            | v.micro
        )

    @staticmethod
    def version_from_migration(number: int) -> str:
        """Return version string encoded by ``number``."""
        major = number >> PackageRelease._MAJOR_SHIFT
        minor = (number >> PackageRelease._MINOR_SHIFT) & PackageRelease._MINOR_MASK
        patch = number & PackageRelease._PATCH_MASK
        return f"{major}.{minor}.{patch}"

    @property
    def is_published(self) -> bool:
        """Return ``True`` if this release has been published."""
        return bool(self.pypi_url)

    @property
    def is_current(self) -> bool:
        """Return ``True`` when this release's version matches the VERSION file
        and its package is active."""
        version_path = Path("VERSION")
        if not version_path.exists():
            return False
        current_version = version_path.read_text().strip()
        return current_version == self.version and self.package.is_active

    @classmethod
    def latest(cls):
        """Return the latest release by version, preferring active packages."""
        from packaging.version import Version

        releases = list(cls.objects.filter(package__is_active=True))
        if not releases:
            releases = list(cls.objects.all())
        if not releases:
            return None
        return max(releases, key=lambda r: Version(r.version))

    @classmethod
    def matches_revision(cls, version: str, revision: str) -> bool:
        """Return ``True`` when *revision* matches the stored release revision.

        When the release metadata cannot be retrieved (for example during
        database initialization), the method optimistically returns ``True`` so
        callers continue operating without raising secondary errors.
        """

        version = cls.strip_dev_suffix((version or "").strip())
        revision = (revision or "").strip()
        if not version or not revision:
            return True

        try:
            queryset = cls.objects.filter(version=version)
            release_revision = (
                queryset.filter(package__is_active=True)
                .values_list("revision", flat=True)
                .first()
            )
            if release_revision is None:
                release_revision = queryset.values_list("revision", flat=True).first()
        except DatabaseError:  # pragma: no cover - depends on DB availability
            logger.debug(
                "PackageRelease.matches_revision skipped: database unavailable",
                exc_info=True,
            )
            return True

        if not release_revision:
            return True
        return release_revision.strip() == revision

    def build(self, **kwargs) -> None:
        """Wrapper around :func:`core.release.build` for convenience."""
        from .. import release as release_utils

        release_utils.build(
            package=self.to_package(),
            version=self.version,
            creds=self.to_credentials(),
            **kwargs,
        )
        self.revision = revision_utils.get_revision()
        self.save(update_fields=["revision"])
        PackageRelease.dump_fixture()
        if kwargs.get("git"):
            from glob import glob

            paths = sorted(glob("apps/core/fixtures/releases__*.json"))
            diff = subprocess.run(
                ["git", "status", "--porcelain", *paths],
                capture_output=True,
                text=True,
            )
            if diff.stdout.strip():
                release_utils._run(["git", "add", *paths])
                release_utils._run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"chore: update release fixture for v{self.version}",
                    ]
                )
                release_utils._run(["git", "push"])

    @property
    def revision_short(self) -> str:
        return self.revision[-6:] if self.revision else ""


def validate_relative_url(value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        raise ValidationError("URL must be relative")
